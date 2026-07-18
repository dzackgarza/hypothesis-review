"""CLI tests for the batch and ledger commands.

Stubs stand in for the Postgres source and h API client, injected via ``ctx.obj``
exactly as ``test_cli_pull.py`` does -- they supply controlled annotation data so these
tests exercise the CLI's own windowing/output/tagging. The boundaries themselves are
proved against the live stack in ``test_source.py`` / ``test_api.py``. The ledger
command runs against a throwaway git repo.
"""

import json
import subprocess
from datetime import datetime, timedelta

from click.testing import CliRunner

from annotate.api import HClient
from annotate.cli import App, main
from annotate.config import Config
from annotate.models import Annotation


def _ann(id, created, tags=None):
    return Annotation(
        id=id,
        created=created,
        userid="acct:me@localhost",
        group="grp",
        uri=f"http://localhost/{id}.html",
        text=f"note {id}",
        tags=tags or [],
        target=[{"selector": [{"type": "TextQuoteSelector", "exact": id}]}],
    )


class _StubSource:
    """In-memory AnnotationSource honoring the since/until window contract."""

    def __init__(self, anns):
        self._anns = anns

    def list(self, group_id, since=None, until=None):
        rows = [a for a in self._anns if a.group == group_id]
        if since is not None:
            rows = [a for a in rows if a.created > since]
        if until is not None:
            rows = [a for a in rows if a.created <= until]
        return sorted(rows, key=lambda a: a.created)


class _StubClient(HClient):
    """Records marker/tag writes without opening an httpx client."""

    def __init__(self):
        self.markers: list[tuple[str, str]] = []
        self.tagged: list[tuple[str, list[str]]] = []

    def create_marker(self, group_id: str, kind: str) -> str:
        self.markers.append((group_id, kind))
        return "m1"

    def tag(self, annotation_id: str, add: list[str]) -> None:
        self.tagged.append((annotation_id, list(add)))


def _app(anns, client=None, cfg=None):
    return App(
        source=_StubSource(anns),
        client=client or _StubClient(),
        group_id="grp",
        cfg=cfg or Config(),
    )


# Int-timestamped session used by resolve/status/ledger (int order == chronological).
OPEN_M = _ann("open", 1, ["review:open"])
A = _ann("a", 2)
B = _ann("b", 3)
SEND_M = _ann("send", 4, ["review:send"])


def test_slice_last_returns_only_in_window_non_markers():
    now = datetime.now()
    recent = _ann("recent", now - timedelta(minutes=30))
    old = _ann("old", now - timedelta(hours=2))
    marker = _ann("open", now - timedelta(minutes=20), ["review:open"])
    result = CliRunner().invoke(main, ["slice", "--last", "1h"], obj=_app([recent, old, marker]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.output)] == ["recent"]


def test_resolve_tags_each_batch_member_acted():
    client = _StubClient()
    result = CliRunner().invoke(main, ["resolve"], obj=_app([OPEN_M, A, B, SEND_M], client=client))
    assert result.exit_code == 0, result.output
    assert client.tagged == [("a", ["acted"]), ("b", ["acted"])]


def test_status_counts_open_annotations():
    acted = _ann("c", 5, ["acted"])
    result = CliRunner().invoke(main, ["status"], obj=_app([OPEN_M, A, B, SEND_M, acted]))
    assert result.exit_code == 0, result.output
    assert "open=2" in result.output
    assert "acted=1" in result.output


def test_status_root_flags_drifted_quote(tmp_path):
    build = tmp_path / "site"
    build.mkdir()
    (build / "a.html").write_text("... the exact quote a is still here ...")
    # A's TextQuote exact is "a" (present in the build); craft one that drifts.
    drift = Annotation(
        id="gone",
        created=6,
        userid="acct:me@localhost",
        group="grp",
        uri="http://localhost/gone.html",
        text="note gone",
        tags=[],
        target=[{"selector": [{"type": "TextQuoteSelector", "exact": "vanished text"}]}],
    )
    result = CliRunner().invoke(main, ["status", "--root", str(build)], obj=_app([A, drift]))
    assert result.exit_code == 0, result.output
    assert "match\ta" in result.output
    assert "drift\tgone" in result.output


# --- ledger command against a throwaway git repo -------------------------------


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _seed_repo(tmp_path):
    repo = tmp_path / "reviewed"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "paper.html").write_text("v1\n")
    _git(repo, "add", "paper.html")
    _git(repo, "commit", "-q", "-m", "v1")
    return repo


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout


def test_ledger_appends_batch_to_named_path_and_commits_it(tmp_path, monkeypatch):
    repo = _seed_repo(tmp_path)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        main, ["ledger", "--path", "feedback/intake.jsonl"], obj=_app([OPEN_M, A, B, SEND_M])
    )
    assert result.exit_code == 0, result.output
    assert [e["id"] for e in json.loads(result.output)] == ["a", "b"]

    ledger_file = repo / "feedback" / "intake.jsonl"
    assert len(ledger_file.read_text().splitlines()) == 2  # one line per real annotation

    committed = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "feedback/intake.jsonl" in committed  # committed, not merely written


def test_ledger_with_no_send_yet_prints_empty_and_makes_no_commit(tmp_path, monkeypatch):
    repo = _seed_repo(tmp_path)
    monkeypatch.chdir(repo)
    before = _head(repo)
    # open marker but no send -> no batch to record
    result = CliRunner().invoke(main, ["ledger"], obj=_app([OPEN_M, A]))
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "[]"
    assert _head(repo) == before  # no empty feedback commit
