"""Session windowing.

A session is a time window: it opens when ``wait`` records the open timestamp locally (no
h write) and closes when the browser calls the loopback close endpoint.
:func:`batch_since` collects the *real* annotations created strictly after the open timestamp
-- already-``acted`` annotations and any legacy ``review:open``/``review:send`` marker rows
(a bad design, no longer emitted) are excluded. A pure function over the
``created``-ordered annotation list.
"""

from __future__ import annotations

import pathlib
from datetime import datetime

from annotate.models import Annotation

#: Where a session's open timestamp is parked, inside the repo's untracked ``.git`` dir so
#: it isolates per-repo (and per test tmp-repo) and ``pull``/``resolve`` can read it back.
OPEN_TIME_REL = pathlib.Path(".git") / "annotate" / "open_time"


def open_time_path(root: pathlib.Path) -> pathlib.Path:
    return root / OPEN_TIME_REL


def write_open_time(root: pathlib.Path, when: datetime) -> None:
    """Record a session's open timestamp under the repo's ``.git`` dir (ISO 8601)."""
    path = open_time_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(when.isoformat(), encoding="utf-8")


def read_open_time(root: pathlib.Path) -> datetime | None:
    """The parked session open timestamp, or ``None`` if no session has been opened."""
    path = open_time_path(root)
    if not path.exists():
        return None
    return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())


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
