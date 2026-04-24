"""Orchestrator + in-memory project store for the Social Media Asset Studio."""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .asset_builder import AssetBuilder
from .brand_research import BrandResearcher
from .models import (
    BrandBrief,
    BuiltAsset,
    MockupAsset,
    Platform,
    ResearchReport,
    StudioProject,
    StyleGuide,
    Vote,
)
from .mockup_generator import MockupGenerator
from .style_guide import StyleGuideGenerator

logger = logging.getLogger(__name__)


class SocialStudioService:
    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        image_model: str = "gpt-image-1",
        research_model: str = "gemini-2.5-pro",
        text_model: str = "gemini-2.5-pro",
        asset_dir: str = "./data/social_assets",
    ) -> None:
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)

        self.researcher = BrandResearcher(gemini_api_key, model=research_model)
        self.style_gen = StyleGuideGenerator(gemini_api_key, model=research_model)
        self.mockups = MockupGenerator(
            openai_api_key=openai_api_key,
            gemini_api_key=gemini_api_key,
            image_model=image_model,
            text_model=text_model,
            asset_dir=str(self.asset_dir),
        )
        self.builder = AssetBuilder(asset_dir=str(self.asset_dir))

        self._projects: Dict[str, StudioProject] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Project lifecycle
    # ------------------------------------------------------------------
    def create_project(self, brief: BrandBrief) -> StudioProject:
        project_id = str(uuid.uuid4())
        project = StudioProject(id=project_id, brief=brief)
        with self._lock:
            self._projects[project_id] = project
        (self.asset_dir / project_id).mkdir(parents=True, exist_ok=True)
        return project

    def get_project(self, project_id: str) -> Optional[StudioProject]:
        return self._projects.get(project_id)

    def _touch(self, project: StudioProject) -> None:
        project.updated_at = datetime.utcnow()

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------
    async def run_research(self, project_id: str) -> ResearchReport:
        project = self._require(project_id)
        project.status = "researching"
        report = await self.researcher.research(project.brief)
        project.research = report
        project.status = "research_ready"
        self._touch(project)
        return report

    async def run_style_guide(self, project_id: str) -> StyleGuide:
        project = self._require(project_id)
        if project.research is None:
            await self.run_research(project_id)
        project.status = "styling"
        guide = await self.style_gen.generate(project.brief, project.research)
        project.style_guide = guide
        project.status = "style_ready"
        self._touch(project)
        return guide

    async def generate_mockups(
        self,
        project_id: str,
        count: int = 16,
        platforms: Optional[List[Platform]] = None,
    ) -> List[MockupAsset]:
        project = self._require(project_id)
        if project.style_guide is None:
            await self.run_style_guide(project_id)
        project.status = "generating_mockups"
        self._touch(project)

        concepts = await self.mockups.plan_concepts(
            project.brief,
            project.research,
            project.style_guide,
            count=count,
            platforms=platforms,
        )
        assets = await self.mockups.render_mockups(project_id, concepts, project.style_guide)
        project.mockups = assets
        project.status = "mockups_ready"
        self._touch(project)
        return assets

    def apply_votes(self, project_id: str, votes: List[Vote]) -> List[MockupAsset]:
        project = self._require(project_id)
        index = {m.concept.id: m for m in project.mockups}
        for v in votes:
            if v.mockup_id in index:
                mockup = index[v.mockup_id]
                mockup.votes += 1
                # Weight heart (1) and stars (1-5). Downvotes (-1) subtract.
                mockup.score += float(v.value)
        project.mockups.sort(key=lambda m: (-m.score, -m.votes))
        self._touch(project)
        return project.mockups

    def update_mockup(
        self,
        project_id: str,
        mockup_id: str,
        updates: Dict[str, object],
    ) -> MockupAsset:
        """Patch editable concept fields on a mockup (headline, cta, etc.)."""
        project = self._require(project_id)
        for asset in project.mockups:
            if asset.concept.id != mockup_id:
                continue
            concept = asset.concept
            for field in ("headline", "subheadline", "cta", "body_copy", "visual_prompt"):
                if updates.get(field) is not None:
                    setattr(concept, field, updates[field])
            if updates.get("palette") is not None:
                concept.palette = list(updates["palette"])
            self._touch(project)
            return asset
        raise KeyError(f"mockup {mockup_id} not found")

    async def regenerate_mockup_image(
        self,
        project_id: str,
        mockup_id: str,
        visual_prompt: Optional[str] = None,
    ) -> MockupAsset:
        """Re-render a single mockup's image (optionally with a new visual prompt)."""
        project = self._require(project_id)
        for idx, asset in enumerate(project.mockups):
            if asset.concept.id != mockup_id:
                continue
            if visual_prompt:
                asset.concept.visual_prompt = visual_prompt
            project_dir = self.asset_dir / project_id / "mockups"
            project_dir.mkdir(parents=True, exist_ok=True)
            new_asset = await self.mockups._render_one(
                asset.concept, project.style_guide, project_dir
            )
            # Preserve votes/score across regeneration.
            new_asset.votes = asset.votes
            new_asset.score = asset.score
            project.mockups[idx] = new_asset
            self._touch(project)
            return new_asset
        raise KeyError(f"mockup {mockup_id} not found")

    def build_assets(
        self,
        project_id: str,
        mockup_ids: Optional[List[str]] = None,
    ) -> List[BuiltAsset]:
        project = self._require(project_id)
        if not project.mockups:
            raise ValueError("no mockups generated yet")
        if mockup_ids:
            selected = [m for m in project.mockups if m.concept.id in set(mockup_ids)]
        else:
            ranked = sorted(project.mockups, key=lambda m: (-m.score, -m.votes))
            selected = [m for m in ranked if m.score > 0][:4] or ranked[:4]
        built = self.builder.build_many(project_id, selected, project.style_guide)
        project.built_assets = built
        project.status = "built"
        self._touch(project)
        return built

    # ------------------------------------------------------------------
    # File access
    # ------------------------------------------------------------------
    def mockup_image_path(self, project_id: str, mockup_id: str) -> Optional[Path]:
        project = self._projects.get(project_id)
        if not project:
            return None
        for m in project.mockups:
            if m.concept.id == mockup_id and m.image_path:
                p = Path(m.image_path)
                if p.exists():
                    return p
        return None

    def built_zip_path(self, project_id: str, slug: str) -> Optional[Path]:
        candidate = self.asset_dir / project_id / "built" / f"{slug}.zip"
        return candidate if candidate.exists() else None

    def all_assets_zip_path(self, project_id: str) -> Optional[Path]:
        candidate = self.asset_dir / project_id / "built" / "all-assets.zip"
        return candidate if candidate.exists() else None

    def _require(self, project_id: str) -> StudioProject:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"project {project_id} not found")
        return project


_singleton: Optional[SocialStudioService] = None
_singleton_lock = threading.Lock()


def get_service() -> SocialStudioService:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = SocialStudioService(
                    gemini_api_key=os.getenv("GEMINI_API_KEY"),
                    openai_api_key=os.getenv("OPENAI_API_KEY"),
                    image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
                    research_model=os.getenv("GEMINI_RESEARCH_MODEL", "gemini-2.5-pro"),
                    text_model=os.getenv("GEMINI_RESEARCH_MODEL", "gemini-2.5-pro"),
                    asset_dir=os.getenv("SOCIAL_ASSET_DIR", "./data/social_assets"),
                )
    return _singleton
