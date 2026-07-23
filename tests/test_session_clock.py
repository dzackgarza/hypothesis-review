"""The clock the session window is measured on.

h records ``annotation.created`` in UTC. Every window this tool computes is compared
against that column, so every one of them has to be UTC too. Anywhere the local clock is
used instead, the window is wrong by the machine's offset -- and for a reader east of
Greenwich that means a session that delivers nothing at all, silently, because a batch of
notes created "in the future" by the local clock's reckoning never enters the window.

These tests run under a fixed non-UTC zone so the arithmetic is the same wherever the
suite runs, and against annotations timestamped the way h timestamps them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import close_the_session, free_port

from annotate.api import HClient
from annotate.cli import App, main
from annotate.models import Annotation

#: Eight hours ahead of UTC: the local clock reads later than anything h has just written.
AHEAD_OF_UTC = "Asia/Taipei"


def _utc_now() -> datetime:
    """A timestamp as h writes one: UTC, and naive, as the column hands it back."""
    return datetime.now(UTC).replace(tzinfo=None)


def _ann(identifier: str, created: datetime) -> Annotation:
    return Annotation(
        id=identifier,
        created=created,
        userid="acct:me@localhost",
        group="grp",
        uri=f"http://localhost/{identifier}",
        text=identifier,
        tags=[],
        target=None,
    )


class _RecordingSource:
    """Answers with its annotations, and remembers the window it was asked for."""

    def __init__(self, anns: list[Annotation]) -> None:
        self._anns = anns
        self.since: datetime | None = None

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        self.since = since
        rows = [a for a in self._anns if a.group == group_id]
        if since is not None:
            rows = [a for a in rows if a.created > since]
        if until is not None:
            rows = [a for a in rows if a.created <= until]
        return sorted(rows, key=lambda a: a.created)


class _StubClient(HClient):
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.tagged: list[tuple[str, list[str]]] = []

    def delete(self, annotation_id: str) -> None:
        self.deleted.append(annotation_id)

    def tag(self, annotation_id: str, add: list[str]) -> None:
        self.tagged.append((annotation_id, list(add)))


def test_a_session_delivers_the_notes_written_during_it_east_of_greenwich(git_repo: Path, ahead_of_utc: None) -> None:
    # The reader opens a session, writes a note, and closes it from the browser. The note
    # is minutes old by h's clock; by the local clock it has not happened yet.
    source = _RecordingSource([_ann("a", _utc_now() + timedelta(seconds=1))])
    port = free_port()
    closer = close_the_session(port, [])

    result = CliRunner().invoke(
        main,
        ["wait", "--timeout", "20", "--port", str(port)],
        obj=App(source=source, client=_StubClient(), group_id="grp"),
    )
    closer.join(timeout=5)

    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a"]


def test_a_relative_window_is_measured_on_the_same_clock_h_writes(git_repo: Path, ahead_of_utc: None) -> None:
    # `slice --last 1h` asks for the last hour of annotations. Measured on the local clock
    # east of Greenwich that is a window ending nine hours in h's future, which quietly
    # contains everything, and west of it a window that contains nothing.
    source = _RecordingSource([])

    CliRunner().invoke(
        main,
        ["slice", "--last", "1h"],
        obj=App(source=source, client=_StubClient(), group_id="grp"),
    )

    assert source.since is not None
    drift = abs((source.since - (_utc_now() - timedelta(hours=1))).total_seconds())
    assert drift < 60, f"the window is {drift / 3600:.0f}h off h's clock"


@pytest.fixture
def ahead_of_utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the test on a machine east of Greenwich, wherever the suite is running."""
    monkeypatch.setenv("TZ", AHEAD_OF_UTC)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
