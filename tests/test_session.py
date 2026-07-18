import pytest

from annotate.models import Annotation
from annotate.session import NoSend, batch_for, latest_open


def _ann(id: str, created: int, tags: list[str] | None = None) -> Annotation:
    return Annotation(
        id=id,
        created=created,
        userid="acct:dzack@localhost",
        group="grp-xyz",
        uri=f"http://localhost/{id}.html",
        text=id,
        tags=tags or [],
    )


OPEN = _ann("open", 1, ["review:open"])
A = _ann("a", 2)  # paperA
B = _ann("b", 3)  # paperB
SEND = _ann("send", 4, ["review:send"])
C = _ann("c", 5)  # post-send


def test_batch_for_windows_between_open_and_send():
    anns = [OPEN, A, B, SEND, C]
    batch = batch_for(anns, OPEN)
    assert batch.annotations == [A, B]  # cross-page, excludes markers and post-send c
    assert batch.open_marker is OPEN
    assert batch.send_marker is SEND


def test_batch_for_excludes_acted():
    acted = _ann("acted-one", 2, ["acted"])
    anns = [OPEN, acted, B, SEND]
    assert batch_for(anns, OPEN).annotations == [B]


def test_batch_for_raises_nosend_when_no_send_yet():
    with pytest.raises(NoSend):
        batch_for([OPEN, A, B], OPEN)


def test_latest_open_returns_last_open_or_none():
    later_open = _ann("open2", 6, ["review:open"])
    assert latest_open([OPEN, A, SEND, later_open]) is later_open
    assert latest_open([A, B]) is None
