"""Math-quote normalizer: the owned logic (detection, alttext recovery, substitution).

The garble→LaTeX conversion is proven end-to-end on real arXiv content elsewhere; these
tests pin the owned logic against a compact MathML fixture that mirrors the real structure
(presentation glyph + content-MathML glyph + the x-tex annotation), so they are
deterministic and need no network.
"""

from annotate.mathquote import apply_math_map, has_math, math_map_from_html

# No whitespace between tags -> predictable textContent (as the browser captures it).
FIXTURE = (
    "<p>Let "
    '<math alttext="f(x,y)"><semantics><mi>f</mi>'
    '<annotation-xml encoding="MathML-Content"><ci>\U0001d453</ci></annotation-xml>'
    '<annotation encoding="application/x-tex">f(x,y)</annotation></semantics></math>'
    " be a "
    '<math alttext="\\tau"><mi>τ</mi>'
    '<annotation-xml><ci>\U0001d70f</ci></annotation-xml>'
    '<annotation encoding="application/x-tex">\\tau</annotation></math>'
    "-invariant polynomial.</p>"
)


def test_has_math_flags_math_block_chars_not_prose():
    assert has_math("Let f\U0001d453f(x,y) be")  # math-italic f is a strong signal
    assert not has_math("An ordinary sentence with no mathematics at all.")


def test_math_map_recovers_latex_from_alttext():
    assert set(math_map_from_html(FIXTURE).values()) == {"$f(x,y)$", "$\\tau$"}


def test_apply_math_map_substitutes_the_garbled_spans():
    math_map = math_map_from_html(FIXTURE)
    garbled = "Let f\U0001d453f(x,y) be a τ\U0001d70f\\tau-invariant polynomial."
    assert apply_math_map(garbled, math_map) == (
        "Let $f(x,y)$ be a $\\tau$-invariant polynomial."
    )


def test_apply_math_map_leaves_plain_text_untouched():
    assert apply_math_map("no math here", {"x\U0001d465": "$x$"}) == "no math here"
