"""Turn a voted-on mockup into a shippable asset bundle.

Each built asset is:
  - a background image (the generated mockup)
  - live HTML + CSS that overlays headline / subheadline / CTA on top
  - a rendered flat PNG (for direct posting or preview)
  - a ZIP of all of the above

The HTML is self-contained and can be opened in a browser for live editing.
"""
from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import BuiltAsset, MockupAsset, PLATFORM_SPECS, StyleGuide

logger = logging.getLogger(__name__)


def _escape_html(text: str) -> str:
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _best_text_color(hex_bg: str) -> str:
    hex_bg = hex_bg.lstrip("#")
    if len(hex_bg) != 6:
        return "#FFFFFF"
    r, g, b = (int(hex_bg[i : i + 2], 16) for i in (0, 2, 4))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#0E1116" if luminance > 0.6 else "#FFFFFF"


def _safe_slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()
    return s or "asset"


class AssetBuilder:
    def __init__(self, asset_dir: str = "./data/social_assets") -> None:
        self.asset_dir = Path(asset_dir)

    def build_many(
        self,
        project_id: str,
        mockups: List[MockupAsset],
        style_guide: StyleGuide,
    ) -> List[BuiltAsset]:
        out_dir = self.asset_dir / project_id / "built"
        out_dir.mkdir(parents=True, exist_ok=True)
        built: List[BuiltAsset] = []
        for mockup in mockups:
            try:
                built.append(self._build_one(project_id, mockup, style_guide, out_dir))
            except Exception as exc:
                logger.exception("Asset build failed for %s: %s", mockup.concept.id, exc)
        # Bundle the collection into a master zip too
        if built:
            self._bundle_all(project_id, built, out_dir)
        return built

    def _build_one(
        self,
        project_id: str,
        mockup: MockupAsset,
        style_guide: StyleGuide,
        out_dir: Path,
    ) -> BuiltAsset:
        concept = mockup.concept
        spec = PLATFORM_SPECS[concept.platform]
        slug = _safe_slug(f"{concept.campaign}-{concept.mood or concept.platform}-{concept.id[:6]}")
        asset_dir = out_dir / slug
        asset_dir.mkdir(parents=True, exist_ok=True)

        # Copy background
        bg_src = Path(mockup.image_path)
        bg_dst = asset_dir / "background.png"
        shutil.copyfile(bg_src, bg_dst)

        # Pick fonts from style guide
        display_font = next(
            (f for f in style_guide.fonts if f.role == "display"),
            style_guide.fonts[0] if style_guide.fonts else None,
        )
        body_font = next(
            (f for f in style_guide.fonts if f.role == "body"),
            style_guide.fonts[-1] if style_guide.fonts else None,
        )
        display_family = (display_font.google_font if display_font else "Space Grotesk") or "Space Grotesk"
        body_family = (body_font.google_font if body_font else "Inter") or "Inter"

        # Accent color from palette
        primary = next(
            (c for c in style_guide.colors if (c.role or "").startswith("primary")),
            style_guide.colors[0] if style_guide.colors else None,
        )
        accent = next(
            (c for c in style_guide.colors if (c.role or "").startswith("accent")),
            style_guide.colors[1] if len(style_guide.colors) > 1 else primary,
        )
        primary_hex = primary.hex if primary else "#0E1116"
        accent_hex = accent.hex if accent else "#FF5A1F"
        on_accent = _best_text_color(accent_hex)

        html = self._render_html(
            concept=concept,
            spec=spec,
            display_family=display_family,
            body_family=body_family,
            primary_hex=primary_hex,
            accent_hex=accent_hex,
            on_accent=on_accent,
        )
        css = self._render_css(
            spec=spec,
            display_family=display_family,
            body_family=body_family,
            primary_hex=primary_hex,
            accent_hex=accent_hex,
            on_accent=on_accent,
        )

        (asset_dir / "post.html").write_text(html, encoding="utf-8")
        (asset_dir / "styles.css").write_text(css, encoding="utf-8")

        # Flat render for direct posting
        render_path = asset_dir / "render.png"
        self._render_flat_png(bg_dst, render_path, concept, spec, primary_hex, accent_hex, on_accent)

        # Per-asset zip
        zip_path = asset_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in asset_dir.iterdir():
                zf.write(p, arcname=f"{slug}/{p.name}")

        return BuiltAsset(
            mockup_id=concept.id,
            html=html,
            css=css,
            background_path=str(bg_dst),
            render_path=str(render_path),
            download_url=f"/api/social/projects/{project_id}/built/{slug}.zip",
            zip_url=f"/api/social/projects/{project_id}/built/{slug}.zip",
        )

    def _bundle_all(self, project_id: str, built: List[BuiltAsset], out_dir: Path) -> Path:
        all_zip = out_dir / "all-assets.zip"
        with zipfile.ZipFile(all_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for asset in built:
                folder = Path(asset.background_path).parent
                for p in folder.iterdir():
                    zf.write(p, arcname=f"{folder.name}/{p.name}")
        return all_zip

    def _render_html(
        self,
        concept,
        spec,
        display_family: str,
        body_family: str,
        primary_hex: str,
        accent_hex: str,
        on_accent: str,
    ) -> str:
        title = _escape_html(concept.headline)
        sub = _escape_html(concept.subheadline or "")
        cta = _escape_html(concept.cta)
        body = _escape_html(concept.body_copy or "")
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family={display_family.replace(' ', '+')}:wght@700;800&family={body_family.replace(' ', '+')}:wght@400;500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <!-- Platform: {spec['label']} ({spec['w']}x{spec['h']}) -->
  <article class="post post--{concept.platform}" contenteditable="true" spellcheck="false">
    <img class="post__bg" src="background.png" alt="" />
    <div class="post__scrim"></div>
    <header class="post__header">
      <h1 class="post__headline">{title}</h1>
      {f'<p class="post__subheadline">{sub}</p>' if sub else ''}
    </header>
    <footer class="post__footer">
      <span class="post__cta">{cta}</span>
    </footer>
  </article>
  {f'<p class="post__caption" contenteditable="true">{body}</p>' if body else ''}
</body>
</html>
"""

    def _render_css(
        self,
        spec,
        display_family: str,
        body_family: str,
        primary_hex: str,
        accent_hex: str,
        on_accent: str,
    ) -> str:
        w, h = spec["w"], spec["h"]
        return f""":root {{
  --brand-primary: {primary_hex};
  --brand-accent: {accent_hex};
  --brand-on-accent: {on_accent};
  --post-w: {w}px;
  --post-h: {h}px;
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: '{body_family}', system-ui, sans-serif;
  background: #111;
  color: #fff;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px;
}}

.post {{
  position: relative;
  width: min(100%, var(--post-w));
  aspect-ratio: {w} / {h};
  overflow: hidden;
  border-radius: 20px;
  box-shadow: 0 30px 80px rgba(0,0,0,.35);
  color: #fff;
}}
.post__bg {{
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  object-fit: cover;
  user-select: none;
  -webkit-user-drag: none;
}}
.post__scrim {{
  position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,.05) 0%, rgba(0,0,0,.45) 70%, rgba(0,0,0,.65) 100%);
}}
.post__header {{
  position: absolute;
  left: 6%; right: 6%; top: 8%;
  z-index: 2;
}}
.post__headline {{
  font-family: '{display_family}', system-ui, sans-serif;
  font-weight: 800;
  font-size: clamp(28px, 7vw, 84px);
  line-height: 1.02;
  letter-spacing: -0.02em;
  margin: 0;
  text-shadow: 0 2px 20px rgba(0,0,0,.35);
}}
.post__subheadline {{
  margin-top: 12px;
  font-size: clamp(14px, 2.2vw, 22px);
  font-weight: 500;
  opacity: .9;
  max-width: 32ch;
  text-shadow: 0 1px 10px rgba(0,0,0,.3);
}}
.post__footer {{
  position: absolute;
  left: 6%; bottom: 7%;
  z-index: 2;
}}
.post__cta {{
  display: inline-block;
  padding: 14px 22px;
  border-radius: 999px;
  background: var(--brand-accent);
  color: var(--brand-on-accent);
  font-weight: 600;
  font-size: clamp(14px, 1.6vw, 18px);
  letter-spacing: .02em;
}}
.post__caption {{
  margin-top: 16px;
  max-width: var(--post-w);
  font-size: 15px;
  color: #ddd;
  line-height: 1.5;
}}
"""

    def _render_flat_png(
        self,
        bg_path: Path,
        out_path: Path,
        concept,
        spec,
        primary_hex: str,
        accent_hex: str,
        on_accent: str,
    ) -> None:
        """Bake text into a flat PNG for posting. Uses PIL default font -
        real typefaces live in the HTML version."""
        w, h = spec["w"], spec["h"]
        img = Image.open(bg_path).convert("RGB").resize((w, h), Image.LANCZOS)

        # Dark scrim at the bottom for text legibility
        scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        scrim_draw = ImageDraw.Draw(scrim)
        for y in range(h):
            t = y / h
            alpha = int(max(0, (t - 0.4)) * 200)
            scrim_draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")

        draw = ImageDraw.Draw(img)
        # PIL default font only supports one size when bitmap; use truetype if available
        headline_size = max(int(h * 0.065), 28)
        sub_size = max(int(h * 0.028), 16)
        cta_size = max(int(h * 0.025), 14)
        headline_font = _load_font(headline_size, bold=True)
        sub_font = _load_font(sub_size)
        cta_font = _load_font(cta_size, bold=True)

        # Headline (wrap manually)
        margin_x = int(w * 0.06)
        max_line_w = w - 2 * margin_x
        headline_lines = _wrap(draw, concept.headline, headline_font, max_line_w)
        y = int(h * 0.08)
        for line in headline_lines:
            draw.text((margin_x, y), line, fill="#FFFFFF", font=headline_font)
            y += int(headline_size * 1.05)

        if concept.subheadline:
            y += int(headline_size * 0.2)
            for line in _wrap(draw, concept.subheadline, sub_font, max_line_w)[:3]:
                draw.text((margin_x, y), line, fill="#F0F0F0", font=sub_font)
                y += int(sub_size * 1.3)

        # CTA pill
        cta_text = concept.cta
        cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
        cta_w = cta_bbox[2] - cta_bbox[0] + int(cta_size * 2)
        cta_h = cta_bbox[3] - cta_bbox[1] + int(cta_size * 1.4)
        cta_x = margin_x
        cta_y = h - int(h * 0.08) - cta_h
        accent_rgb = tuple(int(accent_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        draw.rounded_rectangle(
            [cta_x, cta_y, cta_x + cta_w, cta_y + cta_h],
            radius=cta_h // 2,
            fill=accent_rgb,
        )
        text_fill = on_accent
        draw.text(
            (cta_x + int(cta_size * 1.0), cta_y + int(cta_size * 0.55)),
            cta_text,
            fill=text_fill,
            font=cta_font,
        )

        img.save(out_path, format="PNG", optimize=True)


def _load_font(size: int, bold: bool = False):
    # Try a set of likely-available TTFs, fall back to PIL default.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int):
    words = (text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_w:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
