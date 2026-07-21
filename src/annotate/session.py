"""Session windowing.

A session is a time window: it opens when ``wait`` records the open timestamp locally (no
h write) and closes when the browser calls the loopback close endpoint.
:func:`batch_since` collects the *real* annotations created strictly after the open timestamp
-- already-``acted`` annotations and any legacy ``review:open``/``review:send`` marker rows
(a bad design, no longer emitted) are excluded. A pure function over the
``created``-ordered annotation list.
"""

from __future__ import annotations

from datetime import datetime

from annotate.models import Annotation

OPEN = "review:open"  # legacy session-marker tags, no longer emitted; filtered out of windows
SEND = "review:send"
ACTED = "acted"


def _is_legacy_marker(ann: Annotation) -> bool:
    """A leftover ``review:open``/``review:send`` marker row from the retired marker design."""
    return ann.is_marker(OPEN) or ann.is_marker(SEND)


def batch_since(anns: list[Annotation], since: datetime) -> list[Annotation]:
    """Real annotations created strictly after ``since``.

    Excludes already-``acted`` annotations and any leftover legacy session markers, so the
    result is exactly the reviewable feedback that landed during the open session window.
    """
    return [a for a in anns if a.created > since and not a.is_marker(ACTED) and not _is_legacy_marker(a)]
