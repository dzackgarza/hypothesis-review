"""CLI tests for the recording delivery commands: ``pull`` and ``wait``.

Both record the delivered batch into the git ledger *before* printing it, and bounce unless
run inside a git repo. A session is a time window: ``pull`` reads the open timestamp parked
under ``.git/annotate/open_time`` and delivers the real annotations created since it; ``wait``
parks that timestamp, then serves the loopback session-close endpoint until the browser
posts to it. The ``wait`` tests drive that endpoint for real -- ``--port`` puts the served
socket under the test's control, so the wake, the timeout, and the never-served case are
observed at the real boundary rather than through a substituted ``_park``. Source and client
are stubbed via ``ctx.obj``.
"""

import json
import socket
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import close_the_session, committed_at_head, free_port

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
    """Records what the CLI asked h to do, so a test can assert the drain and its order."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.tagged: list[tuple[str, list[str]]] = []

    def delete(self, annotation_id: str) -> None:
        self.deleted.append(annotation_id)

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


def test_wait_delivers_the_batch_when_the_browser_closes_the_session(git_repo: Path) -> None:
    # The wake is a real POST to the loopback session-close endpoint the command serves,
    # so this exercises the same path the extension drives: park -> serve -> close -> deliver.
    port = free_port()
    statuses: list[int] = []
    closer = close_the_session(port, statuses)
    client = _StubClient()

    result = CliRunner().invoke(
        main,
        ["wait", "--timeout", "20", "--port", str(port)],
        obj=_app([PAST, FUTURE_A, FUTURE_B], client=client),
    )
    closer.join(timeout=5)

    assert result.exit_code == 0, result.output
    assert statuses == [204]  # the served endpoint answered the browser's close request
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]
    assert (git_repo / ".git" / "annotate" / "open_time").exists()  # open time parked locally
    assert client.tagged == []  # wait tags nothing
    assert _ledger_ids(git_repo) == ["a", "b"]  # batch recorded
    # ...and drained: the point of the mechanism is that delivered feedback leaves the
    # reader's sidebar rather than accumulating in it forever.
    assert client.deleted == ["a", "b"]


def test_wait_without_a_timeout_still_serves_and_delivers_on_close(git_repo: Path) -> None:
    # The default is an unbounded wait (no --timeout): annotation is human work with no fixed
    # length. This proves the deadline-less path still parks, serves the close endpoint, and
    # delivers on the real POST -- it would fail on a `wait_for_close(None, ...)` that never
    # served or never returned. The close arrives promptly, so the wait does not hang here.
    port = free_port()
    statuses: list[int] = []
    closer = close_the_session(port, statuses)
    client = _StubClient()

    result = CliRunner().invoke(
        main,
        ["wait", "--port", str(port)],  # no --timeout: block until closed
        obj=_app([PAST, FUTURE_A, FUTURE_B], client=client),
    )
    closer.join(timeout=5)

    assert result.exit_code == 0, result.output
    assert statuses == [204]  # the served endpoint answered the browser's close request
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]
    assert client.deleted == ["a", "b"]  # delivered and drained, same as the bounded path


def test_wait_times_out_when_the_session_is_never_closed(git_repo: Path) -> None:
    # Nobody posts to the served endpoint: the command runs the real timeout to expiry and
    # must deliver nothing, since an unclosed session never defined a review window.
    result = CliRunner().invoke(
        main,
        ["wait", "--timeout", "1", "--port", str(free_port())],
        obj=_app([FUTURE_A, FUTURE_B]),
    )
    assert result.exit_code != 0
    assert "timed out" in result.stderr
    assert _ledger_ids(git_repo) == []  # nothing delivered on timeout


def test_wait_bounces_before_parking_outside_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The port is held by this test for the whole call. Reaching the parking step would
    # therefore fail loudly on the bind -- so the repo-check message is proof that `wait`
    # bounced before ever opening a session it could not record.
    monkeypatch.chdir(tmp_path)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = int(held.getsockname()[1])
        result = CliRunner().invoke(main, ["wait", "--port", str(port)], obj=_app([FUTURE_A, FUTURE_B]))

    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr
    assert not isinstance(result.exception, OSError)  # bounced on the repo check, not the bind


def test_a_failed_record_drains_nothing(git_repo: Path) -> None:
    """The case that would lose a reader's note for good.

    Once an annotation is deleted from h, the ledger holds the only copy of its quote,
    its selectors and its recovered LaTeX. So the write has to come first, and a write
    that fails must leave every note where the reader can still see it.
    """
    port = free_port()
    statuses: list[int] = []
    closer = close_the_session(port, statuses)
    client = _StubClient()
    # The ledger cannot be written: its parent path is a file, not a directory.
    ledger_parent = git_repo / "feedback"
    if ledger_parent.exists():
        for child in ledger_parent.iterdir():
            child.unlink()
        ledger_parent.rmdir()
    ledger_parent.write_text("not a directory")

    result = CliRunner().invoke(
        main,
        ["wait", "--timeout", "20", "--port", str(port)],
        obj=_app([PAST, FUTURE_A], client=client),
    )
    closer.join(timeout=5)

    assert result.exit_code != 0
    assert client.deleted == []
