"""Owned-logic tests for the display-time OCR endpoint.

The OCR itself is Mathpix's and the fingerprint→URL resolution is exercised end-to-end
elsewhere; here we prove the endpoint's own gating: an http URI passes through, a
non-resolvable URI yields no work (no network, no OCR), so a prose or unresolvable quote
never triggers a Mathpix call.
"""

from __future__ import annotations

from annotate.ocr_server import ocr_latex, resolve_pdf_url


def test_resolve_pdf_url_passes_through_http():
    assert resolve_pdf_url("http://x/y.pdf") == "http://x/y.pdf"
    assert resolve_pdf_url("https://arxiv.org/pdf/2312.03638") == "https://arxiv.org/pdf/2312.03638"


def test_resolve_pdf_url_none_for_non_http_non_urn():
    assert resolve_pdf_url("file:///home/x.pdf") is None
    assert resolve_pdf_url("ftp://x/y") is None


def test_ocr_latex_none_without_resolvable_pdf_or_page():
    # No uri, no page, or an unresolvable uri -> nothing to OCR (returns before any network).
    assert ocr_latex({}) is None
    assert ocr_latex({"uri": "file:///x.pdf", "page_index": 0, "exact": "q"}) is None
    assert ocr_latex({"uri": "https://x/y.pdf", "exact": "q"}) is None  # page missing
