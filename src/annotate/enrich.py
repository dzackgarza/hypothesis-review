"""Write recovered PDF math back onto the annotation so it renders in the sidebar.

An HTML annotation's client recovers LaTeX from the page's x-tex layer at display time. A
PDF has no such layer -- the clean LaTeX only exists after OCR, which is server-side (it
needs the Mathpix key). So rather than recover twice, we OCR the region once and PATCH the
clean LaTeX into the annotation *body*: stock Hypothesis renders ``$…$`` in the body
natively (unlike the quote), so the sidebar shows the equation and the agent reads the same
enriched body. The ``target`` selectors (anchoring) are never touched.

Enrichment is idempotent: an already-enriched body carries :data:`ENRICH_MARKER`, so a
re-poll is a no-op, and the user's own note is preserved beneath the recovered equation.
"""

from __future__ import annotations

import os

import psycopg

from annotate.api import HClient
from annotate.config import Config
from annotate.models import Annotation
from annotate.pdfmath import clean_pdf_quote, pdf_has_math

#: Sentinel prefixing a body we have enriched, so the write stays idempotent across polls.
ENRICH_MARKER = "<!-- annotate:math -->"


def enriched_body(clean_latex: str, note: str) -> str:
    """The new body: the recovered equation (marked) above the user's original note."""
    block = f"{ENRICH_MARKER}\n{clean_latex}"
    note = note.strip()
    return f"{block}\n\n{note}" if note else block


def is_enriched(ann: Annotation) -> bool:
    """Whether ``ann``'s body already carries the recovered equation."""
    return ENRICH_MARKER in (ann.text or "")


def resolve_pdf_url(uri: str) -> str | None:
    """A fetchable http(s) URL for a PDF annotation's document, or ``None``. An http URI is
    itself fetchable; a ``urn:x-pdf:`` fingerprint (what a locally-opened PDF anchors to) is
    resolved to a sibling http URL via h's ``document_uri`` table -- a PDF downloaded from
    the web keeps its source URL alongside the fingerprint, so the bytes are fetchable."""
    if uri.startswith(("http://", "https://")):
        return uri
    if not uri.startswith("urn:x-pdf:"):
        return None
    with psycopg.connect(Config.load().pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT du2.uri FROM document_uri du1 JOIN document_uri du2 "
            "ON du1.document_id = du2.document_id "
            "WHERE du1.uri = %s AND du2.uri LIKE 'http%%' LIMIT 1",
            (uri,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def enrich(ann: Annotation, client: HClient) -> bool:
    """OCR a PDF math annotation's region and PATCH the clean LaTeX into its body; return
    whether a write happened. A no-op (``False``) for a non-PDF annotation, one without
    math, one already enriched, a missing OCR key, an unresolvable document, or an OCR miss
    — so it is safe to call on every annotation, every poll. The OCR fetches a resolved http
    URL (see :func:`resolve_pdf_url`); the annotation's ``uri``/selectors are never touched."""
    if ann.page_index is None or not pdf_has_math(ann.quote):
        return False
    if is_enriched(ann) or not os.environ.get("MATHPIX_API_KEY"):
        return False
    url = resolve_pdf_url(ann.uri)
    if url is None:
        return False
    clean = clean_pdf_quote(url, ann.page_index, ann.quote_prefix, ann.quote_suffix, ann.quote)
    if clean == ann.quote:  # locating/OCR miss -> nothing recovered, leave the body alone
        return False
    client.update_text(ann.id, enriched_body(clean, ann.text or ""))
    return True
