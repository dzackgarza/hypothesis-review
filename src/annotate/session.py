"""Session windowing.

A session is the ``review:open`` .. ``review:send`` marker window (see
:mod:`annotate.models`). :func:`batch_for` collects the *real* annotations that
fall strictly after an open marker and up to (and including the timestamp of)
the first send marker following it — markers and already-``acted`` annotations
are excluded. Pure functions over the ``created``-ordered annotation list.
"""

from __future__ import annotations

from annotate.models import Annotation, Batch

OPEN = "review:open"
SEND = "review:send"
ACTED = "acted"


class NoSend(Exception):
    """No ``review:send`` marker exists after the open marker yet."""


def _is_real(ann: Annotation) -> bool:
    """A reviewable annotation: neither a marker nor already acted on."""
    return not (ann.is_marker(OPEN) or ann.is_marker(SEND) or ann.is_marker(ACTED))


def latest_open(anns: list[Annotation]) -> Annotation | None:
    """The most recent ``review:open`` marker, or ``None`` if there is none."""
    opens = [a for a in anns if a.is_marker(OPEN)]
    return max(opens, key=lambda a: a.created) if opens else None


def batch_for(anns: list[Annotation], open_marker: Annotation) -> Batch:
    """Real annotations in ``open_marker.created < created <= send.created``,
    where ``send`` is the first ``review:send`` marker after ``open_marker``.

    Raises :class:`NoSend` if the session has not been sent yet.
    """
    sends = [a for a in anns if a.is_marker(SEND) and a.created > open_marker.created]
    if not sends:
        raise NoSend(f"no {SEND} marker after open marker {open_marker.id}")
    send = min(sends, key=lambda a: a.created)
    window = [
        a
        for a in anns
        if _is_real(a) and open_marker.created < a.created <= send.created
    ]
    return Batch(open_marker=open_marker, send_marker=send, annotations=window)
