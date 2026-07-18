"""Recover clean LaTeX for PDF annotations that span rendered math.

A PDF carries no embedded LaTeX (unlike LaTeXML HTML): selecting an equation yields the
flattened text layer, which loses 2D structure and can silently corrupt it — ``½`` read
as ``12``, a fraction shattered across lines. The only recovery is OCR of the rendered
region.

A Hypothesis PDF annotation carries the page (``PageSelector.index``) and clean-prose
``prefix``/``suffix`` around the selection. We fetch the PDF, render that page, crop the
band the annotation occupies (bracketed by the two prose anchors), and OCR the crop with
Mathpix -> clean ``$…$`` LaTeX. This mirrors :mod:`annotate.mathquote` for the HTML case;
like it, it cleans only the *agent's* copy — the stored selectors (anchoring) are never
touched.
"""

from __future__ import annotations

import base64
import os
import re

import fitz  # pymupdf
import httpx

# Greek letters and math operators survive a PDF text-layer selection as ordinary Unicode
# (not the Mathematical Alphanumeric block the HTML garble uses), so they — not that block
# — are the signal that a PDF quote spans math worth OCR'ing.
_PDF_MATH = re.compile(
    "["
    "Ͱ-Ͽ"  # Greek
    "∀-⋿"  # mathematical operators
    "⨀-⫿"  # supplemental math operators
    "⟀-⟿"  # misc math symbols-A
    "←-⇿"  # arrows
    "]"
)

_DPI = 220  # render resolution of the cropped band; reads cleanly for Mathpix
_ANCHOR = 40  # chars of the prefix tail / suffix head used to locate the band


def pdf_has_math(quote: str) -> bool:
    """Whether a PDF text-layer quote plausibly spans math worth OCR'ing — it contains a
    Greek letter or math operator (the symbols a PDF selection keeps as ordinary Unicode).
    """
    return bool(_PDF_MATH.search(quote))


def _anchor(text: str, *, head: bool) -> str:
    """A single-line search needle from a prefix/suffix: its first (``head``) or last line,
    trimmed to :data:`_ANCHOR` chars. ``search_for`` matches within a line, so a needle
    that straddles a line break would never hit."""
    lines = [s for s in text.splitlines() if s.strip()]
    if not lines:
        return ""
    line = lines[0] if head else lines[-1]
    return (line[:_ANCHOR] if head else line[-_ANCHOR:]).strip()


def _band(page: fitz.Page, prefix: str, suffix: str) -> fitz.Rect | None:
    """The full-width strip of ``page`` between the prose ``prefix`` and ``suffix`` — the
    region the annotation occupies. ``None`` when either anchor can't be located, so the
    caller keeps the raw quote rather than OCR the wrong region."""
    top, bottom = 0.0, page.rect.height
    if prefix:
        hits = page.search_for(_anchor(prefix, head=False))
        if not hits:
            return None
        top = max(r.y1 for r in hits)
    if suffix:
        hits = [r for r in page.search_for(_anchor(suffix, head=True)) if r.y0 >= top]
        if not hits:
            return None
        bottom = min(r.y0 for r in hits)
    if bottom <= top:
        return None
    return fitz.Rect(0, top, page.rect.width, bottom)


def _ocr_latex(png: bytes) -> str:
    """Mathpix OCR of a PNG -> its text with math rendered as ``$…$`` LaTeX."""
    key = os.environ.get("MATHPIX_API_KEY")
    if not key:
        raise RuntimeError("MATHPIX_API_KEY not set; cannot OCR PDF math")
    resp = httpx.post(
        "https://api.mathpix.com/v3/text",
        headers={"app_key": key},
        json={
            "src": "data:image/png;base64," + base64.b64encode(png).decode(),
            "formats": ["text"],
            # Hypothesis's markdown renders math only in \(..\) (inline) or $$..$$ (block)
            # delimiters, never single $..$ -- so emit those, which agents read too.
            "math_inline_delimiters": ["\\(", "\\)"],
            "math_display_delimiters": ["$$", "$$"],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


_pdf_cache: dict[str, bytes] = {}


def _fetch_pdf(uri: str) -> bytes:
    """PDF bytes for ``uri`` (http(s)), fetched once and cached so a batch of annotations
    on one document downloads it a single time."""
    if uri not in _pdf_cache:
        _pdf_cache[uri] = httpx.get(uri, timeout=30, follow_redirects=True).content
    return _pdf_cache[uri]


def clean_pdf_quote(uri: str, page_index: int, prefix: str, suffix: str, exact: str) -> str:
    """Clean LaTeX for a PDF annotation, by OCR'ing the region it occupies. Returns
    ``exact`` (the raw text-layer quote) unchanged when the page or region can't be
    resolved, so a locating miss degrades to honest raw text rather than wrong math."""
    doc = fitz.open(stream=_fetch_pdf(uri), filetype="pdf")
    if not 0 <= page_index < doc.page_count:
        return exact
    band = _band(doc[page_index], prefix, suffix)
    if band is None:
        return exact
    png = doc[page_index].get_pixmap(dpi=_DPI, clip=band).tobytes("png")
    return _ocr_latex(png) or exact
