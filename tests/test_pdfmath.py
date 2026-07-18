"""Owned-logic tests for the PDF math normalizer.

The OCR itself is Mathpix's correctness, proven out-of-band. What this module owns, and
what these tests prove, is: detecting that a PDF quote spans math, and locating the
annotation's region from its prose prefix/suffix so the right slice of the page is what
gets OCR'd — or, on a locating miss, that the honest raw quote is kept instead of the
wrong region. Region logic runs against a real PyMuPDF document with text at known
positions: a real boundary, no network, no OCR call.
"""

from __future__ import annotations

import fitz

from annotate import pdfmath


def test_pdf_has_math_fires_on_operators_but_not_plain_prose():
    # Real quotes: an inline relation carries a math operator; a plain sentence does not.
    assert pdfmath.pdf_has_math("They satisfy 2K ∼ 0 and q = 0")  # ∼ is a math operator
    assert pdfmath.pdf_has_math("the involution ιdP on X")  # ι is Greek
    assert not pdfmath.pdf_has_math("parameterizes the same surfaces, with finite data attached")


def _doc_with_lines(lines: list[tuple[float, str]]) -> fitz.Document:
    """A one-page PDF with each ``(baseline_y, text)`` drawn at the left margin."""
    doc = fitz.open()
    page = doc.new_page()
    for y, text in lines:
        page.insert_text((72, y), text, fontsize=11)
    return doc


def test_band_brackets_exactly_the_region_between_prose_anchors():
    doc = _doc_with_lines(
        [
            (100, "ending the setup paragraph here"),
            (140, "OMEGA equals the residue integral"),
            (180, "One has the following corollary"),
        ]
    )
    page = doc[0]
    band = pdfmath._band(page, "ending the setup paragraph here", "One has the following corollary")
    assert band is not None
    captured = page.get_textbox(band)
    assert "residue integral" in captured  # the region the annotation occupies
    assert "setup paragraph" not in captured  # prefix line excluded
    assert "corollary" not in captured  # suffix line excluded


def test_band_is_none_when_an_anchor_is_absent():
    page = _doc_with_lines([(100, "only line on the page")])[0]
    assert pdfmath._band(page, "no such prefix text here", "no such suffix") is None


def test_clean_pdf_quote_keeps_raw_when_page_is_out_of_range():
    doc = _doc_with_lines([(100, "single page document")])
    uri = "http://test.invalid/a.pdf"
    pdfmath._pdf_cache[uri] = doc.tobytes()  # seed the fetch cache: real bytes, no network
    assert pdfmath.clean_pdf_quote(uri, 5, "", "", "the raw exact quote") == "the raw exact quote"


def test_clean_pdf_quote_keeps_raw_when_region_cannot_be_located():
    doc = _doc_with_lines([(100, "some unrelated prose")])
    uri = "http://test.invalid/b.pdf"
    pdfmath._pdf_cache[uri] = doc.tobytes()
    # A prefix that isn't on the page -> no band -> raw quote, never an OCR of the wrong region.
    assert pdfmath.clean_pdf_quote(uri, 0, "anchor not present", "", "the raw exact quote") == "the raw exact quote"
