"""Style guide generation - turns brief + research into a structured brand system."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional

from .models import (
    BrandBrief,
    ResearchReport,
    StyleGuide,
    ColorToken,
    FontToken,
    CampaignIdea,
)

logger = logging.getLogger(__name__)


STYLE_PROMPT = """You are a world-class brand designer. Produce a concise, opinionated brand style guide that a creative team can use to generate social campaigns tomorrow.

Return STRICT JSON (no markdown fences) matching this schema:

{
  "brand_essence": "one-sentence north star",
  "voice": "short description of how the brand speaks",
  "tone_attributes": ["confident", "warm", ...],
  "colors": [
    {"name": "Primary", "hex": "#123456", "role": "primary"},
    {"name": "Accent",  "hex": "#abcdef", "role": "accent"},
    ... 4-6 total, including at least one dark neutral and one light neutral
  ],
  "fonts": [
    {"name": "Display", "role": "display", "google_font": "Space Grotesk", "fallback": "sans-serif"},
    {"name": "Body",    "role": "body",    "google_font": "Inter",         "fallback": "sans-serif"}
  ],
  "imagery_direction": "paragraph describing photo/illustration style, lighting, composition, subjects, what to avoid",
  "key_messages": ["short punchy statement", ...],
  "do_say": ["example on-brand line", ...],
  "dont_say": ["example off-brand line", ...],
  "campaign_ideas": [
    {
      "title": "Campaign Name",
      "hook": "one-line big idea",
      "narrative": "2-3 sentence story arc",
      "primary_cta": "verb-first CTA",
      "suggested_platforms": ["instagram_square", "linkedin_feed"]
    },
    ... 3-5 total
  ]
}

Make sure every hex is a valid 6-digit hex. Make campaign ideas distinct from each other (different angles, moods, audiences)."""


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


def _valid_hex(h: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", h or ""))


class StyleGuideGenerator:
    def __init__(self, gemini_api_key: Optional[str], model: str = "gemini-2.5-pro") -> None:
        self.model_name = model
        if gemini_api_key and gemini_api_key != "demo":
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                self._genai = genai
            except Exception as exc:
                logger.warning("Could not initialize Gemini for style guide: %s", exc)
                self._genai = None
        else:
            self._genai = None

    async def generate(self, brief: BrandBrief, research: ResearchReport) -> StyleGuide:
        if self._genai is None:
            return self._fallback_style_guide(brief, research)
        try:
            guide = await asyncio.to_thread(self._run_sync, brief, research)
            if guide:
                return guide
        except Exception as exc:
            logger.exception("Style guide generation failed: %s", exc)
        return self._fallback_style_guide(brief, research)

    def _run_sync(self, brief: BrandBrief, research: ResearchReport) -> Optional[StyleGuide]:
        prompt = f"""{STYLE_PROMPT}

=== BRIEF ===
Company: {brief.company_name}
Industry: {brief.industry or 'unspecified'}
Audience: {brief.target_audience or 'unspecified'}
Goals: {brief.goals}
Strategy: {brief.strategy or 'unspecified'}
Tone (user says): {brief.tone or 'unspecified'}
Campaign theme: {brief.campaign_theme or 'none'}

=== RESEARCH ===
Summary: {research.summary}
Positioning: {research.positioning}
Channel observations: {research.current_channel_observations}
Observed themes: {', '.join(research.content_themes_observed)}
Opportunities: {', '.join(research.opportunities)}
"""
        model = self._genai.GenerativeModel(self.model_name)
        response = model.generate_content(prompt)
        text = getattr(response, "text", None)
        parsed = _extract_json(text or "")
        if not parsed:
            return None

        colors = []
        for c in parsed.get("colors", []) or []:
            hex_v = (c.get("hex") or "").strip()
            if not hex_v.startswith("#"):
                hex_v = "#" + hex_v
            if _valid_hex(hex_v):
                colors.append(ColorToken(name=c.get("name", "Color"), hex=hex_v, role=c.get("role")))
        fonts = [
            FontToken(
                name=f.get("name", "Font"),
                role=f.get("role", "body"),
                google_font=f.get("google_font"),
                fallback=f.get("fallback", "sans-serif"),
            )
            for f in parsed.get("fonts", []) or []
        ]
        campaigns = [
            CampaignIdea(
                title=ci.get("title", "Untitled"),
                hook=ci.get("hook", ""),
                narrative=ci.get("narrative", ""),
                primary_cta=ci.get("primary_cta", "Learn more"),
                suggested_platforms=ci.get("suggested_platforms", []) or [],
            )
            for ci in parsed.get("campaign_ideas", []) or []
        ]
        return StyleGuide(
            brand_essence=parsed.get("brand_essence", ""),
            voice=parsed.get("voice", ""),
            tone_attributes=parsed.get("tone_attributes", []) or [],
            colors=colors or self._fallback_palette(),
            fonts=fonts or self._fallback_fonts(),
            imagery_direction=parsed.get("imagery_direction", ""),
            key_messages=parsed.get("key_messages", []) or [],
            do_say=parsed.get("do_say", []) or [],
            dont_say=parsed.get("dont_say", []) or [],
            campaign_ideas=campaigns or self._fallback_campaigns(brief),
        )

    def _fallback_palette(self):
        return [
            ColorToken(name="Ink", hex="#0E1116", role="primary"),
            ColorToken(name="Signal", hex="#FF5A1F", role="accent"),
            ColorToken(name="Cloud", hex="#F5F5F2", role="neutral-light"),
            ColorToken(name="Graphite", hex="#2A2F36", role="neutral-dark"),
            ColorToken(name="Mint", hex="#A6F0C6", role="secondary"),
        ]

    def _fallback_fonts(self):
        return [
            FontToken(name="Display", role="display", google_font="Space Grotesk"),
            FontToken(name="Body", role="body", google_font="Inter"),
        ]

    def _fallback_campaigns(self, brief: BrandBrief):
        return [
            CampaignIdea(
                title=f"Why {brief.company_name}",
                hook=f"The clearest reason to choose {brief.company_name}.",
                narrative="A three-post arc that tells our origin, our promise, and one proof point.",
                primary_cta="See how it works",
                suggested_platforms=["instagram_square", "linkedin_feed"],
            ),
            CampaignIdea(
                title="Proof in Motion",
                hook="Real customers, real wins.",
                narrative="Short-form customer stories that turn outcomes into social proof.",
                primary_cta="Read the story",
                suggested_platforms=["instagram_story", "twitter_post"],
            ),
            CampaignIdea(
                title="Behind the Craft",
                hook="How we build what we build.",
                narrative="Team-led content that humanizes the brand and deepens trust.",
                primary_cta="Meet the team",
                suggested_platforms=["linkedin_feed", "instagram_square"],
            ),
        ]

    def _fallback_style_guide(self, brief: BrandBrief, research: ResearchReport) -> StyleGuide:
        return StyleGuide(
            brand_essence=f"{brief.company_name} helps {brief.target_audience or 'its customers'} win.",
            voice=brief.tone or "Clear, confident, human.",
            tone_attributes=["confident", "warm", "direct"],
            colors=self._fallback_palette(),
            fonts=self._fallback_fonts(),
            imagery_direction=(
                "Editorial photography with natural light, generous negative space, and real people. "
                "Avoid stock cliches and heavy filters."
            ),
            key_messages=[
                f"{brief.company_name} is built for {brief.target_audience or 'your team'}.",
                f"{brief.goals}",
            ],
            do_say=["Plain-spoken benefits", "Specific proof", "Confident calls to action"],
            dont_say=["Jargon-heavy hype", "Vague superlatives", "Passive voice"],
            campaign_ideas=self._fallback_campaigns(brief),
        )
