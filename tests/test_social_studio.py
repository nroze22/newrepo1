"""Tests for the Social Media Asset Studio pipeline.

Covers:
- JSON extraction tolerance (code fences, prose, malformed)
- Fallback concept generation (works offline, emits v2 fields)
- AssetBuilder ZIP contents
- Service round-trip persistence (save -> rehydrate -> match)
- Campaign Kit expansion produces sibling assets at correct sizes
- Capabilities reporting
- OpenAI transient error classification
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from backend.social_studio.brand_research import _extract_json as research_extract
from backend.social_studio.style_guide import _extract_json as style_extract
from backend.social_studio.mockup_generator import (
    _extract_json as mockup_extract,
    MockupGenerator,
)
from backend.social_studio.models import (
    BrandBrief,
    PLATFORM_SPECS,
    StudioProject,
)
from backend.social_studio.service import SocialStudioService


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here you go:\n```\n{"a": 1, "b": [2, 3]}\n```\nThanks!', {"a": 1, "b": [2, 3]}),
    ('Random prose. {"a": 1} done.', {"a": 1}),
])
def test_extract_json_tolerates_common_shapes(text, expected):
    for fn in (research_extract, style_extract, mockup_extract):
        assert fn(text) == expected


@pytest.mark.parametrize("text", ["", None, "no json here", "{not json}"])
def test_extract_json_handles_garbage(text):
    for fn in (research_extract, style_extract, mockup_extract):
        assert fn(text or "") in (None,)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_studio():
    with tempfile.TemporaryDirectory() as d:
        # No keys -> exercises the fully-offline path.
        svc = SocialStudioService(
            gemini_api_key=None,
            openai_api_key=None,
            asset_dir=d,
        )
        yield svc, Path(d)


@pytest.fixture
def offline_brief():
    return BrandBrief(
        company_name="Acme Coffee",
        goals="Drive trial of cold brew",
        target_audience="Urban professionals 25-40",
        platforms=["instagram_square", "instagram_story", "linkedin_feed"],
    )


# ---------------------------------------------------------------------------
# Fallback concept generator emits all v2 fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_pipeline_emits_v2_fields(tmp_studio, offline_brief):
    svc, _ = tmp_studio
    p = svc.create_project(offline_brief)
    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    mocks = await svc.generate_mockups(p.id, count=4)

    assert len(mocks) == 4
    for m in mocks:
        c = m.concept
        assert c.headline
        assert c.cta
        assert c.visual_prompt
        assert len(c.variants) >= 1, "every concept ships >=1 variant offline"
        assert len(c.hashtags) >= 1
        assert c.alt_text
        assert c.caption


# ---------------------------------------------------------------------------
# AssetBuilder produces correct ZIP contents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_built_zip_contains_expected_files(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p = svc.create_project(offline_brief)
    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    mocks = await svc.generate_mockups(p.id, count=2)
    built = svc.build_assets(p.id, mockup_ids=[mocks[0].concept.id])

    assert built, "build returned empty"
    zips = list((root / p.id / "built").glob("*.zip"))
    individual = [z for z in zips if z.name != "all-assets.zip"]
    assert individual, "no per-asset zip"
    with zipfile.ZipFile(individual[0]) as zf:
        names = {n.split("/")[-1] for n in zf.namelist()}
    expected = {"post.html", "styles.css", "background.png", "render.png", "caption.md"}
    assert expected.issubset(names), f"missing {expected - names}"


# ---------------------------------------------------------------------------
# Logo upload composites into render.png
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logo_uploaded_and_included_in_zip(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p = svc.create_project(offline_brief)

    # Synthesize a valid logo.
    img = Image.new("RGBA", (200, 60), (255, 255, 255, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    logo = svc.set_logo(p.id, "logo.png", buf.getvalue())
    assert Path(logo.path).exists()

    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    mocks = await svc.generate_mockups(p.id, count=1)
    built = svc.build_assets(p.id, mockup_ids=[mocks[0].concept.id])
    assert built

    zips = [z for z in (root / p.id / "built").glob("*.zip") if z.name != "all-assets.zip"]
    with zipfile.ZipFile(zips[0]) as zf:
        names = {n.split("/")[-1] for n in zf.namelist()}
    assert "logo.png" in names


def test_set_logo_rejects_garbage(tmp_studio, offline_brief):
    svc, _ = tmp_studio
    p = svc.create_project(offline_brief)
    with pytest.raises(ValueError):
        svc.set_logo(p.id, "logo.png", b"not an image")


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_state_round_trips_through_disk(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p = svc.create_project(offline_brief)
    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    await svc.generate_mockups(p.id, count=2)

    # state.json should exist
    state = root / p.id / "state.json"
    assert state.exists(), "state.json should be persisted"

    # Hydrate a fresh service - it should rediscover the project.
    svc2 = SocialStudioService(asset_dir=str(root))
    rehydrated = svc2.get_project(p.id)
    assert rehydrated is not None
    assert rehydrated.brief.company_name == p.brief.company_name
    assert len(rehydrated.mockups) == 2
    # Every concept should keep its variants/hashtags/etc through serialization.
    assert rehydrated.mockups[0].concept.hashtags == p.mockups[0].concept.hashtags
    assert rehydrated.mockups[0].concept.caption == p.mockups[0].concept.caption


# ---------------------------------------------------------------------------
# Project listing + delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_and_delete(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p1 = svc.create_project(offline_brief)
    p2 = svc.create_project(offline_brief.model_copy(update={"company_name": "Other Co"}))

    listed = svc.list_projects()
    ids = {x["id"] for x in listed}
    assert p1.id in ids and p2.id in ids

    svc.delete_project(p1.id)
    assert svc.get_project(p1.id) is None
    assert not (root / p1.id).exists()


# ---------------------------------------------------------------------------
# Campaign Kit expansion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_campaign_kit_produces_correct_aspect_ratios(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p = svc.create_project(offline_brief)
    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    mocks = await svc.generate_mockups(p.id, count=1)
    base = mocks[0]
    # base is whichever platform the planner picked; targets are everything else.
    kit = await svc.adapt_mockup_to_platforms(p.id, base.concept.id)
    assert kit, "kit should not be empty"
    base_platform = base.concept.platform
    seen = {k.concept.platform for k in kit}
    assert base_platform not in seen, "kit must not duplicate the base platform"
    for k in kit:
        spec = PLATFORM_SPECS[k.concept.platform]
        assert k.width == spec["w"]
        assert k.height == spec["h"]
        assert k.kit_parent_id == base.concept.id
        assert Path(k.image_path).exists()


@pytest.mark.asyncio
async def test_build_expands_kit_into_multi_format_zips(tmp_studio, offline_brief):
    svc, root = tmp_studio
    p = svc.create_project(offline_brief)
    await svc.run_research(p.id)
    await svc.run_style_guide(p.id)
    mocks = await svc.generate_mockups(p.id, count=1)
    base = mocks[0]
    await svc.adapt_mockup_to_platforms(p.id, base.concept.id)

    built = svc.build_assets(p.id, mockup_ids=[base.concept.id])
    # base + every sibling => >= 2 zips
    assert len(built) >= 2
    zips = [z for z in (root / p.id / "built").glob("*.zip") if z.name != "all-assets.zip"]
    assert len(zips) == len(built)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_capabilities_reports_offline_mode(tmp_studio):
    svc, _ = tmp_studio
    caps = svc.capabilities()
    assert caps["openai_image"] is False
    assert caps["gemini_text"] is False
    assert caps["asset_dir_writable"] is True
    assert isinstance(caps["project_count"], int)


# ---------------------------------------------------------------------------
# Transient-error classification on OpenAI calls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc,expected", [
    (Exception("rate limit exceeded"), True),
    (Exception("Server returned 503"), True),
    (Exception("Connection reset"), True),
    (Exception("invalid api key"), False),
    (Exception("billing limit reached"), False),
])
def test_transient_error_classification(exc, expected):
    assert MockupGenerator._is_transient_error(exc) is expected
