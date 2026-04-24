"""Pydantic models for the Social Media Asset Studio workflow."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal

from pydantic import BaseModel, Field, HttpUrl


Platform = Literal[
    "instagram_square",
    "instagram_story",
    "facebook_feed",
    "linkedin_feed",
    "twitter_post",
    "tiktok_story",
    "pinterest_pin",
]


PLATFORM_SPECS: Dict[str, Dict[str, Any]] = {
    "instagram_square": {"label": "Instagram Square", "w": 1080, "h": 1080, "aspect": "1:1"},
    "instagram_story":  {"label": "Instagram Story",  "w": 1080, "h": 1920, "aspect": "9:16"},
    "facebook_feed":    {"label": "Facebook Feed",    "w": 1200, "h": 1200, "aspect": "1:1"},
    "linkedin_feed":    {"label": "LinkedIn Post",    "w": 1200, "h": 1200, "aspect": "1:1"},
    "twitter_post":     {"label": "X / Twitter",      "w": 1600, "h": 900,  "aspect": "16:9"},
    "tiktok_story":     {"label": "TikTok Story",     "w": 1080, "h": 1920, "aspect": "9:16"},
    "pinterest_pin":    {"label": "Pinterest Pin",    "w": 1000, "h": 1500, "aspect": "2:3"},
}


class BrandBrief(BaseModel):
    """Input from the user describing their company + strategy."""
    company_name: str = Field(..., min_length=1, max_length=120)
    website: Optional[str] = Field(None, description="Primary website URL")
    social_handles: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of platform -> handle/url (e.g. {'instagram': '@acme'})"
    )
    goals: str = Field(..., description="High-level marketing / growth goals")
    strategy: Optional[str] = Field(None, description="Company strategy, positioning, differentiators")
    target_audience: Optional[str] = Field(None, description="Who the campaigns should speak to")
    tone: Optional[str] = Field(None, description="Desired voice/tone (e.g. playful, authoritative)")
    industry: Optional[str] = Field(None, description="Industry / vertical")
    campaign_theme: Optional[str] = Field(None, description="Optional seasonal or campaign theme")
    platforms: List[Platform] = Field(
        default_factory=lambda: ["instagram_square", "instagram_story", "linkedin_feed", "twitter_post"]
    )


class ResearchSource(BaseModel):
    title: str
    url: str
    snippet: Optional[str] = None


class ResearchReport(BaseModel):
    """Findings from Gemini's Google Search-grounded research pass."""
    summary: str
    positioning: str
    audience_insights: str
    current_channel_observations: str
    content_themes_observed: List[str] = Field(default_factory=list)
    competitors: List[str] = Field(default_factory=list)
    opportunities: List[str] = Field(default_factory=list)
    sources: List[ResearchSource] = Field(default_factory=list)
    raw_notes: Optional[str] = None


class ColorToken(BaseModel):
    name: str
    hex: str
    role: Optional[str] = None  # primary, secondary, accent, neutral, etc.


class FontToken(BaseModel):
    name: str
    role: str  # display, heading, body
    google_font: Optional[str] = None
    fallback: str = "sans-serif"


class CampaignIdea(BaseModel):
    title: str
    hook: str
    narrative: str
    primary_cta: str
    suggested_platforms: List[str] = Field(default_factory=list)


class StyleGuide(BaseModel):
    """Structured brand style guide produced from research + brief."""
    brand_essence: str
    voice: str
    tone_attributes: List[str] = Field(default_factory=list)
    colors: List[ColorToken] = Field(default_factory=list)
    fonts: List[FontToken] = Field(default_factory=list)
    imagery_direction: str
    key_messages: List[str] = Field(default_factory=list)
    do_say: List[str] = Field(default_factory=list)
    dont_say: List[str] = Field(default_factory=list)
    campaign_ideas: List[CampaignIdea] = Field(default_factory=list)


class MockupConcept(BaseModel):
    """A single creative concept for one social post - pre-image-generation."""
    id: str
    platform: Platform
    campaign: str
    headline: str
    subheadline: Optional[str] = None
    cta: str
    body_copy: Optional[str] = None
    visual_prompt: str = Field(..., description="Prompt fed to the image model")
    palette: List[str] = Field(default_factory=list, description="Hex colors used")
    mood: str = ""


class MockupAsset(BaseModel):
    """A concept + its rendered mockup image."""
    concept: MockupConcept
    image_url: str = Field(..., description="Relative URL to download the PNG")
    image_path: Optional[str] = None
    width: int
    height: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    votes: int = 0
    score: float = 0.0


class Vote(BaseModel):
    mockup_id: str
    value: int = Field(..., ge=-1, le=5, description="-1 downvote, 0 neutral, 1-5 stars")


class VoteBatch(BaseModel):
    votes: List[Vote]


class BuiltAsset(BaseModel):
    """A production-ready asset: bg image + live text rendered as HTML/CSS."""
    mockup_id: str
    html: str
    css: str
    background_path: str
    render_path: str  # final PNG path
    download_url: str
    zip_url: Optional[str] = None


class StudioProject(BaseModel):
    """Top-level project object held in memory (or in a store)."""
    id: str
    brief: BrandBrief
    research: Optional[ResearchReport] = None
    style_guide: Optional[StyleGuide] = None
    mockups: List[MockupAsset] = Field(default_factory=list)
    built_assets: List[BuiltAsset] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "draft"


class CreateProjectResponse(BaseModel):
    project_id: str
    status: str


class GenerateMockupsRequest(BaseModel):
    count: int = Field(16, ge=4, le=16)
    platforms: Optional[List[Platform]] = None


class BuildAssetsRequest(BaseModel):
    mockup_ids: Optional[List[str]] = Field(
        None,
        description="If omitted, uses the top-voted mockups (up to 4)."
    )
