"""CLI tests for slice (read-only view + guidance), record (append by id), resolve, status.

slice/resolve/status touch no ledger, so they need no repo; record commits into the
throwaway ``git_repo`` fixture. Stubs supply annotation data via ``ctx.obj``.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from conftest import committed_at_head

from annotate.api import HClient
from annotate.cli import App, main
from annotate.config import Config
from annotate.models import Annotation
from annotate.session import write_open_time


def _ann(id: str, created: Any, tags: list[str] | None = None) -> Annotation:
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
    """Records ``acted`` tag writes without opening an httpx client."""

    def __init__(self) -> None:
        self.tagged: list[tuple[str, list[str]]] = []

    def tag(self, annotation_id: str, add: list[str]) -> None:
        self.tagged.append((annotation_id, list(add)))


def _app(anns: list[Annotation], client: HClient | None = None) -> App:
    return App(source=_StubSource(anns), client=client or _StubClient(), group_id="grp")


# Int-timestamped session (int order == chronological).
OPEN_M = _ann("open", 1, ["review:open"])
A = _ann("a", 2)
B = _ann("b", 3)
SEND_M = _ann("send", 4, ["review:send"])


def test_slice_last_returns_only_in_window_non_markers() -> None:
    now = datetime.now()
    recent = _ann("recent", now - timedelta(minutes=30))
    old = _ann("old", now - timedelta(hours=2))
    marker = _ann("open", now - timedelta(minutes=20), ["review:open"])
    result = CliRunner().invoke(main, ["slice", "--last", "1h"], obj=_app([recent, old, marker]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.stdout)] == ["recent"]


def test_slice_points_at_record_for_preservation() -> None:
    now = datetime.now()
    recent = _ann("recent", now - timedelta(minutes=30))
    result = CliRunner().invoke(main, ["slice", "--last", "1h"], obj=_app([recent]))
    assert result.exit_code == 0, result.output
    assert "annotate record recent" in result.stderr  # guidance names the command + id


def test_slice_with_no_hits_gives_no_record_guidance() -> None:
    result = CliRunner().invoke(main, ["slice", "--last", "1h"], obj=_app([]))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert "annotate record" not in result.stderr


def test_record_appends_named_annotations_and_commits(git_repo: Path) -> None:
    result = CliRunner().invoke(main, ["record", "a", "b"], obj=_app([OPEN_M, A, B, SEND_M]))
    assert result.exit_code == 0, result.output
    assert [e["id"] for e in json.loads(result.stdout)] == ["a", "b"]  # recorded entries
    ledger = git_repo / "feedback" / "ledger.jsonl"
    assert [json.loads(line)["id"] for line in ledger.read_text().splitlines()] == ["a", "b"]
    assert "feedback/ledger.jsonl" in committed_at_head(git_repo)


def test_record_rejects_an_unknown_id(git_repo: Path) -> None:
    result = CliRunner().invoke(main, ["record", "nope"], obj=_app([OPEN_M, A, B, SEND_M]))
    assert result.exit_code != 0
    assert "not in the group" in result.stderr


def test_record_bounces_outside_a_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["record", "a"], obj=_app([A]))
    assert result.exit_code != 0
    assert "not inside a git repository" in result.stderr


def test_resolve_tags_each_batch_member_acted(git_repo: Path) -> None:
    write_open_time(git_repo, datetime(2026, 7, 20, 12, 0, 0))
    ra = _ann("a", datetime(2026, 7, 20, 12, 0, 1))
    rb = _ann("b", datetime(2026, 7, 20, 12, 0, 2))
    client = _StubClient()
    result = CliRunner().invoke(main, ["resolve"], obj=_app([ra, rb], client=client))
    assert result.exit_code == 0, result.output
    assert client.tagged == [("a", ["acted"]), ("b", ["acted"])]


def test_resolve_without_an_open_session_exits_nonzero(git_repo: Path) -> None:
    # hypothesis-review#7: resolving with no session open must fail loudly, not tag an
    # empty batch and report success.
    client = _StubClient()
    result = CliRunner().invoke(main, ["resolve"], obj=_app([A], client=client))
    assert result.exit_code != 0
    assert "no review session is open" in result.output
    assert client.tagged == []


def test_status_counts_open_annotations() -> None:
    acted = _ann("c", 5, ["acted"])
    result = CliRunner().invoke(main, ["status"], obj=_app([OPEN_M, A, B, SEND_M, acted]))
    assert result.exit_code == 0, result.output
    assert "open=2" in result.stdout
    assert "acted=1" in result.stdout


def test_status_root_flags_drifted_quote(tmp_path: Path) -> None:
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
    assert "match\ta" in result.stdout
    assert "drift\tgone" in result.stdout


def test_doctor_fails_and_explains_outside_a_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["doctor"], obj=_app([]))
    assert result.exit_code != 0  # a failing check exits non-zero so agents can gate on it
    assert "[FAIL] git repo" in result.stdout
    assert "no feedback can be recorded" in result.stdout


def test_doctor_reports_git_repo_and_where_feedback_lands(git_repo: Path) -> None:
    app = App(
        source=_StubSource([]),
        client=_StubClient(),
        group_id="g",
        cfg=Config(
            api_url="http://localhost:5000",
            pg_dsn="postgresql://localhost/h",
            group_id="g",
            token="6879-x",
        ),
    )
    result = CliRunner().invoke(main, ["doctor"], obj=app)
    assert "[OK] git repo" in result.stdout
    assert "feedback/ledger.jsonl" in result.stdout  # tells the agent where feedback lands
    assert "[OK] config" in result.stdout


def test_status_root_larger_than_the_read_bound_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Drift detection reads the whole tree into memory; past the declared bound that
    # must be a loud error, not an unbounded memory balloon.
    monkeypatch.setattr("annotate.cli._BUILD_TEXT_MAX_BYTES", 8)
    build = tmp_path / "site"
    build.mkdir()
    (build / "a.html").write_text("more than eight bytes of build text")

    result = CliRunner().invoke(main, ["status", "--root", str(build)], obj=_app([A]))

    assert result.exit_code != 0
    assert "drift detection" in result.output
