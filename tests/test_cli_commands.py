"""CLI tests for the batch/ledger/anchor commands (Task 8).

Stubs stand in for the Postgres source and h API client, injected via
``ctx.obj`` exactly as ``test_cli_pull.py`` does. ``rewind``/``delta`` run
against a throwaway git repo (``core.hooksPath=/dev/null`` isolates it from the
machine-wide commit gate, mirroring ``test_anchor.py``).
"""

import json
import subprocess
from datetime import datetime, timedelta

from click.testing import CliRunner

from annotate.api import HClient
from annotate.cli import App, main
from annotate.config import Config
from annotate.models import Annotation, LedgerEntry


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


# Int-timestamped session used by resolve/status (int ordering == chronological).
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


def test_ledger_appends_commit_stamped_entries(tmp_path):
    deploy_log = tmp_path / "deploy-log.tsv"
    deploy_log.write_text("2026-07-18T09:00:00\tsha_live\n")
    ledger_path = tmp_path / "ledger.jsonl"
    cfg = Config(ledger_path=ledger_path, deploy_log=deploy_log)
    anns = [
        _ann("open", "2026-07-18T09:30:00", ["review:open"]),
        _ann("a", "2026-07-18T10:00:00"),
        _ann("b", "2026-07-18T10:30:00"),
        _ann("send", "2026-07-18T11:00:00", ["review:send"]),
    ]
    result = CliRunner().invoke(main, ["ledger"], obj=_app(anns, cfg=cfg))
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert [e["id"] for e in printed] == ["a", "b"]
    assert all(e["commit"] == "sha_live" and e["state"] == "open" for e in printed)
    assert len(ledger_path.read_text().splitlines()) == 2


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


# --- rewind / delta against a real throwaway git repo ---


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
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
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _entry(id, commit, uri="paper.html"):
    return LedgerEntry(
        id=id,
        created="2026-07-18T10:00:00",
        uri=uri,
        text="fix",
        tags=[],
        target=None,
        commit=commit,
        state="open",
    )


def _write_ledger(tmp_path, *entries):
    p = tmp_path / "ledger.jsonl"
    p.write_text("".join(e.to_json() + "\n" for e in entries))
    return p


def test_rewind_looks_up_entry_and_prints_checkout_cmd(tmp_path, monkeypatch):
    repo, sha = _seed_repo(tmp_path)
    ledger_path = _write_ledger(
        tmp_path,
        _entry("other", "0" * 40),  # decoy with a bogus commit
        _entry("a1", sha),
    )
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        main, ["rewind", "a1"], obj=_app([], cfg=Config(ledger_path=ledger_path))
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"git checkout {sha}"


def test_delta_diffs_annotated_page_since_commit(tmp_path, monkeypatch):
    repo, sha = _seed_repo(tmp_path)
    (repo / "paper.html").write_text("v2 changed\n")
    _git(repo, "commit", "-q", "-am", "v2")
    ledger_path = _write_ledger(tmp_path, _entry("a1", sha))
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        main, ["delta", "a1"], obj=_app([], cfg=Config(ledger_path=ledger_path))
    )
    assert result.exit_code == 0, result.output
    assert "v1" in result.output and "v2 changed" in result.output
