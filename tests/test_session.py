from datetime import datetime

from annotate.models import Annotation
from annotate.session import batch_since


def _ann(id: str, created: datetime, tags: list[str] | None = None) -> Annotation:
    return Annotation(
        id=id,
        created=created,
        userid="acct:dzack@localhost",
        group="grp-xyz",
        uri=f"http://localhost/{id}.html",
        text=id,
        tags=tags or [],
    )


T0 = datetime(2026, 7, 20, 12, 0, 0)  # session open time
BEFORE = _ann("before", datetime(2026, 7, 20, 11, 0, 0))
A = _ann("a", datetime(2026, 7, 20, 12, 0, 1))
B = _ann("b", datetime(2026, 7, 20, 12, 0, 2))


def test_batch_since_returns_reals_created_after_open() -> None:
    assert batch_since([BEFORE, A, B], T0) == [A, B]  # excludes the pre-open annotation


def test_batch_since_excludes_acted() -> None:
    acted = _ann("acted-one", datetime(2026, 7, 20, 12, 0, 1), ["acted"])
    assert batch_since([acted, B], T0) == [B]


def test_batch_since_excludes_legacy_markers() -> None:
    open_marker = _ann("open", datetime(2026, 7, 20, 12, 0, 1), ["review:open"])
    send_marker = _ann("send", datetime(2026, 7, 20, 12, 0, 3), ["review:send"])
    assert batch_since([open_marker, A, B, send_marker], T0) == [A, B]


def test_batch_since_is_exclusive_at_the_open_instant() -> None:
    at_open = _ann("at-open", T0)  # created == since is not "after"
    assert batch_since([at_open, A], T0) == [A]
