"""Brand research using Gemini with Google Search grounding.

Produces a structured ResearchReport from a user's brand brief by searching
the live web for the company's website, social channels, press, and
competitors. Falls back to a synthesized (non-grounded) analysis if the
Gemini API key isn't configured or the grounded call fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from .models import BrandBrief, ResearchReport, ResearchSource

logger = logging.getLogger(__name__)


RESEARCH_SYSTEM_PROMPT = """You are a senior brand strategist preparing a research dossier for a social media campaign studio.

Use Google Search to investigate the brand's website, public social channels, press coverage, and any notable competitors. Then return STRICT JSON (no markdown, no prose outside JSON) with this schema:

{
  "summary": "2-4 sentence executive summary of the brand today",
  "positioning": "how the brand positions itself in the market",
  "audience_insights": "who they currently speak to and gaps",
  "current_channel_observations": "concrete observations about their existing website/social presence (tone, visuals, cadence)",
  "content_themes_observed": ["theme 1", "theme 2", ...],
  "competitors": ["competitor 1", "competitor 2", ...],
  "opportunities": ["actionable opportunity 1", ...],
  "raw_notes": "any additional notes the strategist thinks are useful"
}

Be specific. Cite URLs inline inside the text using (source: URL) whenever a claim comes from a search result."""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of a model response."""
    if not text:
        return None
    # Strip code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fallback: find outermost braces
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _sources_from_grounding(grounding_metadata: Any) -> List[ResearchSource]:
    """Pull titles/URLs out of Gemini grounding metadata (best-effort)."""
    sources: List[ResearchSource] = []
    if not grounding_metadata:
        return sources
    # Gemini's grounding metadata varies by SDK version; handle both shapes.
    candidates = []
    for attr in ("grounding_chunks", "groundingChunks"):
        chunks = getattr(grounding_metadata, attr, None)
        if chunks:
            candidates = chunks
            break
    for chunk in candidates or []:
        web = getattr(chunk, "web", None) or (chunk.get("web") if isinstance(chunk, dict) else None)
        if not web:
            continue
        title = getattr(web, "title", None) or (web.get("title") if isinstance(web, dict) else None)
        uri = getattr(web, "uri", None) or (web.get("uri") if isinstance(web, dict) else None)
        if uri:
            sources.append(ResearchSource(title=title or uri, url=uri))
    return sources


class BrandResearcher:
    def __init__(self, gemini_api_key: Optional[str], model: str = "gemini-2.5-pro") -> None:
        self.api_key = gemini_api_key
        self.model_name = model
        self._client = None
        if gemini_api_key and gemini_api_key != "demo":
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                self._genai = genai
            except Exception as exc:
                logger.warning("Could not initialize Gemini for research: %s", exc)
                self._genai = None
        else:
            self._genai = None

    async def research(self, brief: BrandBrief) -> ResearchReport:
        """Run grounded brand research; return a ResearchReport."""
        if self._genai is None:
            return self._fallback_report(brief, reason="no_gemini_key")

        prompt = self._build_prompt(brief)
        try:
            report = await asyncio.to_thread(self._run_grounded, prompt)
            if report:
                return report
        except Exception as exc:
            logger.exception("Grounded research failed: %s", exc)
        return self._fallback_report(brief, reason="grounded_call_failed")

    def _build_prompt(self, brief: BrandBrief) -> str:
        handles = ", ".join(f"{k}: {v}" for k, v in brief.social_handles.items()) or "none provided"
        return f"""{RESEARCH_SYSTEM_PROMPT}

Company: {brief.company_name}
Website: {brief.website or 'not provided - please search for it'}
Social handles: {handles}
Industry: {brief.industry or 'unspecified'}
Target audience (user says): {brief.target_audience or 'unspecified'}
Goals: {brief.goals}
Strategy / positioning (user says): {brief.strategy or 'unspecified'}
Campaign theme: {brief.campaign_theme or 'none'}

Search the web for this company now. Look at their homepage, about page, blog, and any social profiles you can find. Then return ONLY the JSON object described above."""

    def _run_grounded(self, prompt: str) -> Optional[ResearchReport]:
        """Synchronous call to Gemini with Google Search grounding."""
        genai = self._genai
        tools = None
        # Try the modern tool-config form; fall back to legacy shape.
        try:
            tools = [{"google_search_retrieval": {}}]
            model = genai.GenerativeModel(self.model_name, tools=tools)
            response = model.generate_content(prompt)
        except Exception as exc_modern:
            logger.info("Modern grounding tool not accepted (%s); trying legacy", exc_modern)
            try:
                model = genai.GenerativeModel(self.model_name)
                response = model.generate_content(
                    prompt,
                    tools=[{"google_search": {}}],
                )
            except Exception as exc_legacy:
                logger.warning("Grounded research unavailable: %s", exc_legacy)
                # Last resort: ungrounded call
                model = genai.GenerativeModel(self.model_name)
                response = model.generate_content(prompt)

        text = getattr(response, "text", None)
        if not text and getattr(response, "candidates", None):
            try:
                text = response.candidates[0].content.parts[0].text
            except Exception:
                text = None
        parsed = _extract_json(text or "")
        if not parsed:
            return None

        sources: List[ResearchSource] = []
        for cand in getattr(response, "candidates", []) or []:
            gm = getattr(cand, "grounding_metadata", None)
            sources.extend(_sources_from_grounding(gm))

        return ResearchReport(
            summary=parsed.get("summary", ""),
            positioning=parsed.get("positioning", ""),
            audience_insights=parsed.get("audience_insights", ""),
            current_channel_observations=parsed.get("current_channel_observations", ""),
            content_themes_observed=parsed.get("content_themes_observed", []) or [],
            competitors=parsed.get("competitors", []) or [],
            opportunities=parsed.get("opportunities", []) or [],
            raw_notes=parsed.get("raw_notes"),
            sources=sources,
        )

    def _fallback_report(self, brief: BrandBrief, reason: str) -> ResearchReport:
        """Produce a useful report from the brief alone when the API is unavailable."""
        themes = [brief.campaign_theme] if brief.campaign_theme else []
        themes.extend([
            "mission-driven storytelling",
            "customer proof points",
            "educational content",
        ])
        return ResearchReport(
            summary=(
                f"{brief.company_name} operates in the {brief.industry or 'unspecified'} space. "
                f"Goals: {brief.goals}. Without live web research this summary is derived from the brief."
            ),
            positioning=brief.strategy or "Positioning to be defined by stakeholders.",
            audience_insights=brief.target_audience or "Audience not specified in the brief.",
            current_channel_observations=(
                "Live channel research unavailable (offline mode). Add GEMINI_API_KEY to enable "
                "Google Search-grounded research."
            ),
            content_themes_observed=themes,
            competitors=[],
            opportunities=[
                "Run a 4-week awareness campaign tied to the stated goals.",
                "Launch a short-form educational series on top social channels.",
                "Invest in founder-voice storytelling on LinkedIn.",
            ],
            raw_notes=f"fallback_reason={reason}",
            sources=[],
        )
