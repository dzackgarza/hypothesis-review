"""Recover clean LaTeX for quotes that span rendered math.

Selecting text over rendered math (arXiv/LaTeXML MathML, KaTeX, MathJax) captures a garbled
concatenation of each ``<math>`` element's text layers. The clean source is in the page
though: as the ``<math>`` ``alttext`` attribute or an ``<annotation
encoding="application/x-tex">``. We fetch the annotated page, map each ``<math>``'s garbled
``textContent`` to its ``$latex$``, and substitute those spans in the quote.

This mirrors the browser-side normalizer in the client fork; here it cleans the *agent's*
copy — the quotes recorded in the ledger and returned in the batch. It never mutates the
stored annotation, so anchoring is untouched.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

# Mathematical Alphanumeric Symbols, invisible math operators, or LaTeXML's llamapun
# accessibility markers — strong signals of a garbled ``<math>`` capture, rare in prose.
_MATH_SIGNAL = re.compile(r"[\U0001d400-\U0001d7ff⁡-⁤]|start_POST(?:SUB|SUPER)SCRIPT")


def has_math(quote: str) -> bool:
    """Whether ``quote`` likely spans rendered math (worth fetching the page to clean)."""
    return bool(_MATH_SIGNAL.search(quote))


def math_map_from_html(html: str) -> dict[str, str]:
    """Map each ``<math>``'s garbled ``textContent`` to its ``$latex$`` (from ``alttext`` or
    the ``application/x-tex`` annotation)."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for math in soup.find_all("math"):
        garbled = math.get_text()
        alttext = math.get("alttext")  # bs4 types multi-valued attrs as a list; alttext is CDATA
        tex = alttext if isinstance(alttext, str) else ""
        if not tex:
            ann = math.find("annotation", attrs={"encoding": "application/x-tex"})
            tex = ann.get_text() if ann else ""
        if garbled and tex and garbled not in out:
            out[garbled] = f"${tex.strip()}$"
    return out


def apply_math_map(quote: str, math_map: dict[str, str]) -> str:
    """Replace garbled ``<math>`` spans in ``quote`` with their ``$latex$``. Longest matches
    first, so a formula is never partially rewritten by a shorter sub-span."""
    out = quote
    for garbled in sorted(math_map, key=len, reverse=True):
        if garbled in out:
            out = out.replace(garbled, math_map[garbled])
    return out


_page_cache: dict[str, dict[str, str]] = {}


def clean_quote(uri: str, quote: str) -> str:
    """Clean the math in ``quote`` using the page at ``uri``; returns ``quote`` unchanged
    when it carries no math signal. Page math-maps are cached, so a batch of annotations on
    one page fetches it once."""
    if not has_math(quote):
        return quote
    if uri not in _page_cache:
        html = httpx.get(uri, timeout=10, follow_redirects=True).text
        _page_cache[uri] = math_map_from_html(html)
    return apply_math_map(quote, _page_cache[uri])
