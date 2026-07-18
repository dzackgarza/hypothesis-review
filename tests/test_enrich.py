"""Tests for PDF math enrichment (write recovered LaTeX back onto the annotation).

The OCR itself is Mathpix's, proven out-of-band. What enrichment owns, and what these
prove, is: the idempotency gate (never OCR/rewrite an annotation twice, never touch a
non-PDF or math-free one), the body it writes (recovered equation above the user's note),
and the API write that lands the body while leaving anchoring intact. Gating runs with a
real ``HClient`` that is never contacted (the gate returns before any request); the write
runs against the live h API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
import psycopg
import pytest

from annotate.api import HClient
from annotate.config import Config
from annotate.enrich import ENRICH_MARKER, enrich, enriched_body, is_enriched
from annotate.models import Annotation
from annotate.source import PostgresSource

# A client whose endpoint is never contacted: every gating test must return before any HTTP.
_UNUSED = HClient("http://unused.invalid/api", "unused-token")


def _pdf_ann(*, text: str = "", quote: str = "Let ω be", page_index: int | None = 3) -> Annotation:
    return Annotation(
        id="pub-id", created=datetime(2026, 1, 1), userid="u", group="g",
        uri="https://arxiv.org/pdf/2312.03638", text=text, quote=quote, page_index=page_index,
    )


def test_enriched_body_puts_equation_above_the_users_note():
    body = enriched_body("$\\tau$", "why is this degree 8?")
    assert body.startswith(ENRICH_MARKER)
    assert "$\\tau$" in body
    assert body.endswith("why is this degree 8?")


def test_enriched_body_is_just_the_equation_when_there_is_no_note():
    assert enriched_body("$\\tau$", "   ") == f"{ENRICH_MARKER}\n$\\tau$"


def test_is_enriched_detects_the_marker():
    assert is_enriched(_pdf_ann(text=f"{ENRICH_MARKER}\n$x$"))
    assert not is_enriched(_pdf_ann(text="a plain note"))


def test_enrich_skips_already_enriched_without_writing():
    assert enrich(_pdf_ann(text=f"{ENRICH_MARKER}\n$x$"), _UNUSED) is False


def test_enrich_skips_non_pdf_without_writing():
    assert enrich(_pdf_ann(page_index=None, quote="Let ω be"), _UNUSED) is False


def test_enrich_skips_pdf_without_math_without_writing():
    assert enrich(_pdf_ann(quote="the moduli space of surfaces"), _UNUSED) is False


@pytest.fixture
def seeded_annotation() -> Any:
    """One annotation with a note and a TextQuoteSelector, created via the live h API and
    hard-deleted afterward."""
    cfg = Config.load()
    tag = f"__annotate_it_{uuid.uuid4().hex}"
    page = f"http://example.test/{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={"uri": page, "text": "original note", "tags": [tag], "group": cfg.group_id,
              "target": [{"source": page, "selector": [
                  {"type": "TextQuoteSelector", "exact": "hello world"}]}]},
    )
    resp.raise_for_status()
    yield cfg, tag, resp.json()["id"]
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


@pytest.mark.pg
def test_update_text_replaces_body_and_leaves_anchoring(seeded_annotation: Any) -> None:
    cfg, tag, ann_id = seeded_annotation
    with HClient(cfg.api_url, cfg.token) as client:
        client.update_text(ann_id, f"{ENRICH_MARKER}\n$\\omega$\n\noriginal note")
    [row] = [a for a in PostgresSource(cfg.pg_dsn).list(cfg.group_id) if tag in a.tags]
    assert is_enriched(row)  # body now carries the recovered equation
    assert "$\\omega$" in row.text
    assert row.quote == "hello world"  # TextQuoteSelector (anchoring) untouched by the body write
