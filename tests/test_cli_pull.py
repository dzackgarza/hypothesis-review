"""CLI tests for the recording delivery commands: ``pull`` and ``wait``.

Both record the delivered batch into the git ledger *before* printing it, and bounce unless
run inside a git repo. A session is a time window: ``pull`` reads the open timestamp parked
under ``.git/annotate/open_time`` and delivers the real annotations created since it; ``wait``
parks that timestamp, blocks in ``_park`` until the harness wakes it, then delivers. Tests
monkeypatch ``_park`` rather than racing a real signal. Source and client are stubbed via
``ctx.obj``.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import committed_at_head

from annotate.api import HClient
from annotate.cli import App, main
from annotate.models import Annotation
from annotate.session import write_open_time


def _ann(id: str, created: datetime, tags: list[str] | None = None) -> Annotation:
    return Annotation(
        id=id,
        created=created,
        userid="acct:me@localhost",
        group="grp",
        uri=f"http://localhost/{id}",
        text=id,
        tags=tags or [],
        target=None,
    )


class _StubSource:
    """In-memory AnnotationSource honoring the since/until window contract."""

    def __init__(self, anns: list[Annotation]) -> None:
        self._anns = anns

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        rows = [a for a in self._anns if a.group == group_id]
        if since is not None:
            rows = [a for a in rows if a.created > since]
        if until is not None:
            rows = [a for a in rows if a.created <= until]
        return sorted(rows, key=lambda a: a.created)


class _StubClient(HClient):
    """A do-nothing client: pull/wait never write to h."""

    def __init__(self) -> None:
        self.tagged: list[tuple[str, list[str]]] = []

    def tag(self, annotation_id: str, add: list[str]) -> None:
        self.tagged.append((annotation_id, list(add)))


OPEN_TIME = datetime(2026, 7, 20, 12, 0, 0)
BEFORE = _ann("before", datetime(2026, 7, 20, 11, 0, 0))  # created before the session opened
A = _ann("a", datetime(2026, 7, 20, 12, 0, 1))
B = _ann("b", datetime(2026, 7, 20, 12, 0, 2))
# For the wait tests, which park open=now(): PAST is before any real clock, FUTURE after it.
PAST = _ann("before", datetime(2000, 1, 1, 0, 0, 0))
FUTURE_A = _ann("a", datetime(2099, 1, 1, 0, 0, 1))
FUTURE_B = _ann("b", datetime(2099, 1, 1, 0, 0, 2))


def _app(anns: list[Annotation], client: HClient | None = None) -> App:
    return App(source=_StubSource(anns), client=client or _StubClient(), group_id="grp")


def _ledger_ids(repo: Path) -> list[str]:
    p = repo / "feedback" / "ledger.jsonl"
    return [json.loads(line)["id"] for line in p.read_text().splitlines()] if p.exists() else []


def test_pull_records_and_prints_batch(git_repo: Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    result = CliRunner().invoke(main, ["pull"], obj=_app([BEFORE, A, B]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]  # only the post-open ones
    assert _ledger_ids(git_repo) == ["a", "b"]  # recorded to the default ledger
    assert "feedback/ledger.jsonl" in committed_at_head(git_repo)  # committed
    assert "recorded 2 new" in result.stderr  # audit log to stderr
    assert "default ledger" in result.stderr


def test_pull_rejects_a_highlight_without_backend_normalization(git_repo: Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    missing = Annotation(
        id="missing",
        created=datetime(2026, 7, 20, 12, 0, 1),
        userid="acct:me@localhost",
        group="grp",
        uri="http://localhost/missing",
        text="note",
        target=[{"selector": [{"type": "TextQuoteSelector", "exact": "flattened math"}]}],
        normalization_error="h has no normalized quote; diagnostic abc-123",
    )

    result = CliRunner().invoke(main, ["pull"], obj=_app([missing]))

    assert result.exit_code != 0
    assert "cannot deliver unnormalized annotation" in result.stderr
    assert "diagnostic abc-123" in result.stderr
    assert _ledger_ids(git_repo) == []


def test_pull_bounces_when_not_in_a_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # a bare dir, not a git repo
    result = CliRunner().invoke(main, ["pull"], obj=_app([A, B]))
    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr


def test_pull_dedups_on_a_second_call(git_repo: Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    runner = CliRunner()
    runner.invoke(main, ["pull"], obj=_app([BEFORE, A, B]))
    head_after_first = committed_at_head(git_repo)
    result = runner.invoke(main, ["pull"], obj=_app([BEFORE, A, B]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]  # still delivered
    assert _ledger_ids(git_repo) == ["a", "b"]  # not duplicated
    assert "recorded 0 new" in result.stderr  # nothing new to commit
    assert committed_at_head(git_repo) == head_after_first  # no second commit


def test_pull_records_to_named_path(git_repo: Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    result = CliRunner().invoke(main, ["pull", "--path", "research-intake.jsonl"], obj=_app([A, B]))
    assert result.exit_code == 0, result.output
    assert (git_repo / "research-intake.jsonl").exists()
    assert "research-intake.jsonl" in committed_at_head(git_repo)
    assert "default ledger" not in result.stderr  # a name was supplied


def test_pull_without_an_open_session_exits_nonzero(git_repo: Path) -> None:
    # hypothesis-review#7: a missing session is an error, not an empty successful batch.
    # An agent that runs `pull` before any session opened must learn that, not read `[]`
    # as "the reviewer sent nothing".
    result = CliRunner().invoke(main, ["pull"], obj=_app([A, B]))  # no open_time parked
    assert result.exit_code != 0
    assert "no review session is open" in result.output
    assert _ledger_ids(git_repo) == []  # nothing recorded for the failed delivery


def test_pull_with_an_open_session_and_no_new_annotations_prints_an_empty_batch(
    git_repo: Path,
) -> None:
    # The empty-but-successful case stays distinguishable from the missing-session error:
    # a session IS open, nothing was created inside it, and that is a real empty delivery.
    write_open_time(git_repo, OPEN_TIME)
    result = CliRunner().invoke(main, ["pull"], obj=_app([BEFORE]))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_wait_parks_open_time_then_records_and_prints_batch(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("annotate.cli._park", lambda timeout: True)  # harness woke us
    client = _StubClient()
    result = CliRunner().invoke(main, ["wait"], obj=_app([PAST, FUTURE_A, FUTURE_B], client=client))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]
    assert (git_repo / ".git" / "annotate" / "open_time").exists()  # open time parked locally
    assert client.tagged == []  # wait makes no h write
    assert _ledger_ids(git_repo) == ["a", "b"]  # batch recorded


def test_wait_times_out_without_a_wake(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("annotate.cli._park", lambda timeout: False)  # nobody woke us
    result = CliRunner().invoke(main, ["wait", "--timeout", "1"], obj=_app([FUTURE_A, FUTURE_B]))
    assert result.exit_code != 0
    assert "timed out" in result.stderr
    assert _ledger_ids(git_repo) == []  # nothing delivered on timeout


def test_wait_bounces_before_parking_outside_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    parked: list[bool] = []

    def mark_parked(timeout: int) -> bool:
        parked.append(True)
        return True

    monkeypatch.setattr("annotate.cli._park", mark_parked)
    result = CliRunner().invoke(main, ["wait"], obj=_app([FUTURE_A, FUTURE_B]))
    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr
    assert parked == []  # never parked a session it could not record
