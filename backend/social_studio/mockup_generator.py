"""Mockup concept + image generation.

1. Use Gemini (or local fallback) to plan N diverse campaign concepts.
2. Render each concept with OpenAI's gpt-image-1 (image v2) model in parallel.
3. If no image API key is available, fall back to locally rendered gradient
   placeholders so the rest of the app still works end-to-end.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import (
    BrandBrief,
    CopyVariant,
    MockupAsset,
    MockupConcept,
    Platform,
    PLATFORM_SPECS,
    ResearchReport,
    StyleGuide,
)

logger = logging.getLogger(__name__)


CONCEPT_PROMPT = """You are a senior social media creative director. Produce {count} DISTINCT social post concepts for the brand below. Concepts should vary in platform, mood, angle, and visual treatment - a creative collage that shows breadth.

Return STRICT JSON (no markdown) as an object: { "concepts": [ ... ] }.

Each concept must include:
- "platform": one of {platforms}
- "campaign": which of the style-guide campaigns this belongs to (title)
- "headline": max 6 words, punchy
- "subheadline": optional supporting line, max 10 words
- "cta": verb-first call to action, max 4 words
- "body_copy": optional short paragraph (<= 180 chars) suitable as a feed caption
- "visual_prompt": a vivid 2-4 sentence prompt for an image model. Include subject, setting, lighting, composition, mood, and specific brand colors. DO NOT include any text or typography instructions in the visual prompt - text will be overlaid separately.
- "palette": list of 2-4 hex strings drawn from the brand palette that should dominate the image
- "mood": one-word mood descriptor
- "variants": array of exactly 2 alternate copy sets for A/B testing, each with {"headline","subheadline","cta"}. Variants should differ meaningfully from the primary (different angle, different hook) while staying on-brand.
- "caption": platform-native caption (IG/LinkedIn = ~2 sentences warm and informative; X = <= 240 chars with a hook; TikTok = 1 hooky sentence). DIFFERENT from the body_copy - this is the in-feed post text.
- "hashtags": 5-8 relevant hashtags as short strings WITHOUT the # prefix.
- "alt_text": single-sentence accessibility description of the image for screen readers.

Vary dramatically across concepts - different subjects, settings, crops, energy levels."""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) != 6:
        return (80, 80, 80)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


class MockupGenerator:
    def __init__(
        self,
        openai_api_key: Optional[str],
        gemini_api_key: Optional[str],
        image_model: str = "gpt-image-1",
        text_model: str = "gemini-2.5-pro",
        asset_dir: str = "./data/social_assets",
    ) -> None:
        self.image_model = image_model
        self.text_model = text_model
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)

        self._openai = None
        # Cap concurrent OpenAI image calls so we don't trip rate limits even
        # when the user asks for a 16-tile collage.
        self._openai_sem = asyncio.Semaphore(int(os.getenv("OPENAI_IMAGE_CONCURRENCY", "4")))
        if openai_api_key and openai_api_key != "demo":
            try:
                from openai import AsyncOpenAI
                self._openai = AsyncOpenAI(api_key=openai_api_key)
            except Exception as exc:
                logger.warning("Could not initialize OpenAI client: %s", exc)

        self._genai = None
        if gemini_api_key and gemini_api_key != "demo":
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                self._genai = genai
            except Exception as exc:
                logger.warning("Could not initialize Gemini for concepts: %s", exc)

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------
    async def plan_concepts(
        self,
        brief: BrandBrief,
        research: ResearchReport,
        style_guide: StyleGuide,
        count: int,
        platforms: Optional[List[Platform]] = None,
    ) -> List[MockupConcept]:
        target_platforms = platforms or brief.platforms or ["instagram_square"]
        if self._genai is not None:
            try:
                concepts = await asyncio.to_thread(
                    self._plan_via_gemini, brief, research, style_guide, count, target_platforms
                )
                if concepts:
                    return concepts
            except Exception as exc:
                logger.exception("Gemini concept planning failed: %s", exc)
        return self._fallback_concepts(brief, style_guide, count, target_platforms)

    def _plan_via_gemini(
        self,
        brief: BrandBrief,
        research: ResearchReport,
        style_guide: StyleGuide,
        count: int,
        platforms: List[str],
    ) -> List[MockupConcept]:
        palette_hex = [c.hex for c in style_guide.colors]
        campaigns = [c.title for c in style_guide.campaign_ideas]
        prompt = CONCEPT_PROMPT.format(count=count, platforms=platforms) + f"""

=== BRAND ===
Company: {brief.company_name}
Essence: {style_guide.brand_essence}
Voice: {style_guide.voice}
Tone: {', '.join(style_guide.tone_attributes)}
Imagery direction: {style_guide.imagery_direction}
Palette (hex): {palette_hex}
Campaigns: {campaigns}
Goals: {brief.goals}
Audience: {brief.target_audience or 'general'}
Campaign theme: {brief.campaign_theme or 'none'}
"""
        model = self._genai.GenerativeModel(self.text_model)
        response = model.generate_content(prompt)
        text = getattr(response, "text", None) or ""
        parsed = _extract_json(text)
        if not parsed or "concepts" not in parsed:
            return []

        concepts: List[MockupConcept] = []
        for raw in parsed.get("concepts", [])[:count]:
            platform = raw.get("platform") or platforms[0]
            if platform not in PLATFORM_SPECS:
                platform = platforms[0]
            variants = []
            for v in raw.get("variants", []) or []:
                if not isinstance(v, dict):
                    continue
                variants.append(
                    CopyVariant(
                        headline=(v.get("headline") or "").strip() or "",
                        subheadline=v.get("subheadline"),
                        cta=(v.get("cta") or "").strip() or "Learn more",
                    )
                )
            hashtags = [
                h.lstrip("#").strip()
                for h in (raw.get("hashtags") or [])
                if isinstance(h, str) and h.strip()
            ][:10]
            concepts.append(
                MockupConcept(
                    id=str(uuid.uuid4()),
                    platform=platform,
                    campaign=raw.get("campaign", campaigns[0] if campaigns else "Signature"),
                    headline=raw.get("headline", "").strip() or brief.company_name,
                    subheadline=raw.get("subheadline"),
                    cta=raw.get("cta", "Learn more"),
                    body_copy=raw.get("body_copy"),
                    visual_prompt=raw.get("visual_prompt", ""),
                    palette=[p for p in raw.get("palette", []) if isinstance(p, str)][:4],
                    mood=raw.get("mood", ""),
                    variants=variants[:3],
                    caption=raw.get("caption"),
                    hashtags=hashtags,
                    alt_text=raw.get("alt_text"),
                )
            )
        return concepts

    def _fallback_concepts(
        self,
        brief: BrandBrief,
        style_guide: StyleGuide,
        count: int,
        platforms: List[str],
    ) -> List[MockupConcept]:
        palette = [c.hex for c in style_guide.colors] or ["#0E1116", "#FF5A1F", "#F5F5F2"]
        campaigns = style_guide.campaign_ideas or []
        moods = ["bold", "calm", "vibrant", "minimal", "cinematic", "playful", "editorial", "gritty"]
        concepts: List[MockupConcept] = []
        for i in range(count):
            campaign = campaigns[i % max(len(campaigns), 1)] if campaigns else None
            platform = platforms[i % len(platforms)]
            headline = (campaign.hook if campaign else f"{brief.company_name} is here")[:48]
            subheadline = (campaign.narrative[:80] if campaign else brief.goals[:80])
            primary_cta = campaign.primary_cta if campaign else "Learn more"
            # simple mechanical variants so UX is still demonstrable offline
            alt_ctas = ["Try it today", "See how", "Start free", "Book a demo"]
            variants = [
                CopyVariant(
                    headline=f"{brief.company_name} that actually delivers",
                    subheadline=brief.goals[:70],
                    cta=alt_ctas[i % len(alt_ctas)],
                ),
                CopyVariant(
                    headline=(campaign.title if campaign else "Made for you"),
                    subheadline=(brief.target_audience or "").strip()[:80] or None,
                    cta=alt_ctas[(i + 1) % len(alt_ctas)],
                ),
            ]
            hashtags = [
                (brief.company_name or "brand").replace(" ", "").lower(),
                (brief.industry or "marketing").replace(" ", "").lower(),
                "socialmedia",
                "campaign",
                moods[i % len(moods)],
            ]
            concepts.append(
                MockupConcept(
                    id=str(uuid.uuid4()),
                    platform=platform,
                    campaign=campaign.title if campaign else "Signature",
                    headline=headline,
                    subheadline=subheadline,
                    cta=primary_cta,
                    body_copy=None,
                    visual_prompt=(
                        f"A {moods[i % len(moods)]} editorial image for {brief.company_name}. "
                        f"{style_guide.imagery_direction} "
                        f"Dominant colors: {', '.join(palette[:3])}."
                    ),
                    palette=palette[:3],
                    mood=moods[i % len(moods)],
                    variants=variants,
                    caption=(
                        f"{campaign.hook if campaign else brief.goals} "
                        f"— {brief.company_name}."
                    ),
                    hashtags=hashtags,
                    alt_text=(
                        f"A {moods[i % len(moods)]} visual representing "
                        f"{brief.company_name}'s {campaign.title if campaign else 'brand'} campaign."
                    ),
                )
            )
        return concepts

    async def adapt_to_platform(
        self,
        project_id: str,
        base_asset: MockupAsset,
        platform: Platform,
        style_guide: StyleGuide,
    ) -> MockupAsset:
        """Create a sibling mockup for a different platform (different aspect ratio).

        The concept text is preserved; the image is re-rendered at the new
        platform's native size so the Campaign Kit is truly multi-format.
        """
        project_dir = self.asset_dir / project_id / "mockups"
        project_dir.mkdir(parents=True, exist_ok=True)
        sibling_concept = base_asset.concept.model_copy(update={
            "id": str(uuid.uuid4()),
            "platform": platform,
        })
        new_asset = await self._render_one(sibling_concept, style_guide, project_dir)
        new_asset.kit_parent_id = base_asset.concept.id
        return new_asset

    # ------------------------------------------------------------------
    # Image rendering
    # ------------------------------------------------------------------
    async def render_mockups(
        self,
        project_id: str,
        concepts: List[MockupConcept],
        style_guide: StyleGuide,
    ) -> List[MockupAsset]:
        assets: List[MockupAsset] = []
        async for event in self.render_mockups_streaming(project_id, concepts, style_guide):
            if event.get("type") == "tile":
                assets.append(event["asset"])
        return assets

    async def render_mockups_streaming(
        self,
        project_id: str,
        concepts: List[MockupConcept],
        style_guide: StyleGuide,
    ):
        """Yield events as each tile finishes - powers SSE progress streaming."""
        project_dir = self.asset_dir / project_id / "mockups"
        project_dir.mkdir(parents=True, exist_ok=True)

        async def _wrap(concept: MockupConcept) -> MockupAsset:
            try:
                return await self._render_one(concept, style_guide, project_dir)
            except Exception as exc:
                logger.error("Mockup render failed for %s: %s", concept.id, exc)
                return self._render_placeholder(concept, style_guide, project_dir)

        tasks = {asyncio.create_task(_wrap(c)): c for c in concepts}
        total = len(tasks)
        completed = 0
        yield {"type": "plan", "total": total}
        try:
            for fut in asyncio.as_completed(list(tasks.keys())):
                asset = await fut
                completed += 1
                yield {
                    "type": "tile",
                    "completed": completed,
                    "total": total,
                    "asset": asset,
                }
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        yield {"type": "done", "total": total}

    async def _render_one(
        self,
        concept: MockupConcept,
        style_guide: StyleGuide,
        project_dir: Path,
    ) -> MockupAsset:
        if self._openai is not None:
            try:
                return await self._render_openai(concept, project_dir)
            except Exception as exc:
                logger.warning("OpenAI image render failed for %s: %s", concept.id, exc)
        return self._render_placeholder(concept, style_guide, project_dir)

    async def _render_openai(self, concept: MockupConcept, project_dir: Path) -> MockupAsset:
        spec = PLATFORM_SPECS[concept.platform]
        # gpt-image-1 supports: 1024x1024, 1024x1536, 1536x1024, auto
        size = self._nearest_supported_size(spec["w"], spec["h"])
        prompt = self._compose_image_prompt(concept)

        # Bounded concurrency + exponential-backoff retry on transient errors.
        png_bytes: Optional[bytes] = None
        async with self._openai_sem:
            attempts = 3
            delay = 1.5
            for attempt in range(1, attempts + 1):
                try:
                    response = await self._openai.images.generate(
                        model=self.image_model,
                        prompt=prompt,
                        size=size,
                        n=1,
                    )
                    b64 = response.data[0].b64_json
                    png_bytes = base64.b64decode(b64)
                    break
                except Exception as exc:
                    transient = self._is_transient_error(exc)
                    logger.warning(
                        "OpenAI image gen attempt %d/%d failed (%s, transient=%s)",
                        attempt, attempts, exc, transient,
                    )
                    if attempt >= attempts or not transient:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2
        assert png_bytes is not None

        # Resize to exact platform spec
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img = img.resize((spec["w"], spec["h"]), Image.LANCZOS)
        out_path = project_dir / f"{concept.id}.png"
        img.save(out_path, format="PNG", optimize=True)

        return MockupAsset(
            concept=concept,
            image_path=str(out_path),
            image_url=f"/api/social/projects/{project_dir.parent.name}/mockups/{concept.id}/image",
            width=spec["w"],
            height=spec["h"],
        )

    def _nearest_supported_size(self, w: int, h: int) -> str:
        ratio = w / h
        if ratio > 1.2:
            return "1536x1024"
        if ratio < 0.8:
            return "1024x1536"
        return "1024x1024"

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """Classify common OpenAI errors as retryable or not."""
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if any(k in name for k in ("rate", "timeout", "connection", "apiconnection", "internalserver")):
            return True
        if any(k in msg for k in (
            "rate limit", "timeout", "temporarily",
            "connection", "reset", "unavailable",
            "504", "503", "502", "500",
        )):
            return True
        return False

    def _compose_image_prompt(self, concept: MockupConcept) -> str:
        palette_note = ""
        if concept.palette:
            palette_note = f" Use a palette dominated by {', '.join(concept.palette)}."
        mood = f" Mood: {concept.mood}." if concept.mood else ""
        # We deliberately ask for NO text in the generated image - overlay happens in-browser.
        return (
            f"{concept.visual_prompt} {palette_note}{mood} "
            "High-quality editorial photography or illustration suitable as a social post background. "
            "Absolutely no text, no letters, no numbers, no logos, no watermarks - clean image only. "
            "Leave thoughtful negative space suitable for overlaying a short headline."
        )

    # ------------------------------------------------------------------
    # Placeholder renderer (no external API required)
    # ------------------------------------------------------------------
    def _render_placeholder(
        self,
        concept: MockupConcept,
        style_guide: StyleGuide,
        project_dir: Path,
    ) -> MockupAsset:
        spec = PLATFORM_SPECS[concept.platform]
        w, h = spec["w"], spec["h"]
        palette = concept.palette or [c.hex for c in style_guide.colors] or ["#0E1116", "#FF5A1F"]
        c1 = _hex_to_rgb(palette[0])
        c2 = _hex_to_rgb(palette[1] if len(palette) > 1 else palette[0])

        img = Image.new("RGB", (w, h), color=c1)
        # Gradient
        grad = Image.new("RGB", (w, h), color=c1)
        draw = ImageDraw.Draw(grad)
        for y in range(h):
            t = y / max(h - 1, 1)
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

        # Organic blurred blobs for visual interest
        blob = Image.new("RGB", (w, h), color=c1)
        blob_draw = ImageDraw.Draw(blob)
        import random
        rng = random.Random(concept.id)
        for _ in range(6):
            cx = rng.randint(0, w)
            cy = rng.randint(0, h)
            radius = rng.randint(min(w, h) // 8, min(w, h) // 3)
            color = _hex_to_rgb(palette[rng.randint(0, len(palette) - 1)])
            blob_draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius], fill=color
            )
        blob = blob.filter(ImageFilter.GaussianBlur(radius=min(w, h) // 10))

        composed = Image.blend(grad, blob, 0.5)
        out_path = project_dir / f"{concept.id}.png"
        composed.save(out_path, format="PNG", optimize=True)

        return MockupAsset(
            concept=concept,
            image_path=str(out_path),
            image_url=f"/api/social/projects/{project_dir.parent.name}/mockups/{concept.id}/image",
            width=w,
            height=h,
        )
