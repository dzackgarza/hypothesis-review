"""CLI tests for the recording delivery commands: ``pull`` and ``wait``.

Both record the delivered batch into the git ledger *before* printing it, and bounce
unless run inside a git repo. They run against the throwaway ``git_repo`` fixture; source
and client are stubbed via ``ctx.obj``.
"""

import json
from datetime import datetime

from click.testing import CliRunner

from conftest import committed_at_head

from annotate.api import HClient
from annotate.cli import App, main
from annotate.models import Annotation


def _ann(id: str, created: int, tags: list[str] | None = None) -> Annotation:
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
    def __init__(self, anns: list[Annotation]) -> None:
        self._anns = anns

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        rows = [a for a in self._anns if a.group == group_id]
        return sorted(rows, key=lambda a: a.created)


class _StubClient(HClient):
    def __init__(self) -> None:
        self.markers: list[tuple[str, str]] = []

    def create_marker(self, group_id: str, kind: str) -> str:
        self.markers.append((group_id, kind))
        return "marker-1"


OPEN = _ann("open", 1, ["review:open"])
A = _ann("a", 2)
B = _ann("b", 3)
SEND = _ann("send", 4, ["review:send"])


def _app(anns: list[Annotation], client: HClient | None = None) -> App:
    return App(source=_StubSource(anns), client=client or _StubClient(), group_id="grp")


def _ledger_ids(repo) -> list[str]:
    p = repo / "feedback" / "ledger.jsonl"
    return [json.loads(line)["id"] for line in p.read_text().splitlines()] if p.exists() else []


def test_pull_records_and_prints_batch(git_repo):
    result = CliRunner().invoke(main, ["pull"], obj=_app([OPEN, A, B, SEND]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]  # stdout is the batch
    assert _ledger_ids(git_repo) == ["a", "b"]  # recorded to the default ledger
    assert "feedback/ledger.jsonl" in committed_at_head(git_repo)  # committed
    assert "recorded 2 new" in result.stderr  # audit log to stderr
    assert "default ledger" in result.stderr


def test_pull_bounces_when_not_in_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a bare dir, not a git repo
    result = CliRunner().invoke(main, ["pull"], obj=_app([OPEN, A, B, SEND]))
    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr


def test_pull_dedups_on_a_second_call(git_repo):
    runner = CliRunner()
    runner.invoke(main, ["pull"], obj=_app([OPEN, A, B, SEND]))
    head_after_first = committed_at_head(git_repo)
    result = runner.invoke(main, ["pull"], obj=_app([OPEN, A, B, SEND]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]  # still delivered
    assert _ledger_ids(git_repo) == ["a", "b"]  # not duplicated
    assert "recorded 0 new" in result.stderr  # nothing new to commit
    assert committed_at_head(git_repo) == head_after_first  # no second commit


def test_pull_records_to_named_path(git_repo):
    result = CliRunner().invoke(
        main, ["pull", "--path", "research-intake.jsonl"], obj=_app([OPEN, A, B, SEND])
    )
    assert result.exit_code == 0, result.output
    assert (git_repo / "research-intake.jsonl").exists()
    assert "research-intake.jsonl" in committed_at_head(git_repo)
    assert "default ledger" not in result.stderr  # a name was supplied


def test_pull_empty_when_session_opened_but_not_sent(git_repo):
    result = CliRunner().invoke(main, ["pull"], obj=_app([OPEN, A, B]))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert _ledger_ids(git_repo) == []  # nothing to record


def test_wait_opens_marker_then_records_and_prints_batch(git_repo):
    client = _StubClient()
    result = CliRunner().invoke(main, ["wait"], obj=_app([OPEN, A, B, SEND], client=client))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["a", "b"]
    assert client.markers == [("grp", "review:open")]  # exactly one open marker
    assert _ledger_ids(git_repo) == ["a", "b"]  # batch recorded


def test_wait_bounces_before_opening_a_marker_outside_a_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _StubClient()
    result = CliRunner().invoke(main, ["wait"], obj=_app([OPEN, A, B, SEND], client=client))
    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr
    assert client.markers == []  # never opened a session it could not record
