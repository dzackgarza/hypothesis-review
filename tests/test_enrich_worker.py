"""Owned-logic tests for the eager enrichment worker.

The OCR/reconstruction is proven elsewhere; what this module owns is the routing: turning a
public annotation id into its DB uuid, reading the quote/page off the target, and deciding
which annotations it handles (PDF) versus leaves for another path (HTML). No network.
"""

from __future__ import annotations

import base64
import uuid

from annotate import enrich_worker


def test_db_uuid_roundtrips_a_public_id():
    real = uuid.uuid4()
    public_id = base64.urlsafe_b64encode(real.bytes).decode().rstrip("=")
    assert enrich_worker._db_uuid(public_id) == str(real)


def _annotation(selectors: list[dict], uri: str = "https://example.test/a") -> dict:
    return {"uri": uri, "target": [{"selector": selectors}]}


def test_normalize_skips_html_annotations():
    # No PageSelector -> an HTML annotation -> left for the client's reconstruction.
    annotation = _annotation(
        [{"type": "TextQuoteSelector", "exact": "They satisfy 2K ∼ 0"}]
    )
    assert enrich_worker.normalize(annotation) is None


def test_normalize_skips_when_there_is_no_quote():
    annotation = _annotation([{"type": "PageSelector", "index": 0}])
    assert enrich_worker.normalize(annotation) is None


def test_quote_reads_the_text_quote_selector():
    selectors = [
        {"type": "PageSelector", "index": 2},
        {"type": "TextQuoteSelector", "exact": "the residue is 2K"},
    ]
    assert enrich_worker._quote(selectors) == "the residue is 2K"
    assert enrich_worker._quote([{"type": "PageSelector", "index": 2}]) == ""
