"""The normalization gate, proven at each delivering command's own entry point.

annotate promises it will never hand an agent an annotation whose quote h did not
normalize. Four commands make that promise -- ``pull`` and ``wait`` deliver an open
session's batch, ``record`` appends chosen annotations, ``resolve`` writes the batch back
to h -- and each one enforces it through its own call into the shared gate. A shared helper
existing is not evidence that a caller invokes it, which is exactly the gap a green suite
could hide, so every command is driven here through its own entry point and judged only on
what it observably did: its exit status, what reached stdout, what landed in the ledger,
what was written to h.

Each command is run against every state in which no usable normalized quote exists -- h
stored no normalized row at all, h stored an empty one, h stored one that is only
whitespace -- because that is the set the promise covers, not just the database-null state.
Each is also run against a genuinely normalized quote, so the proof is that the gate
discriminates rather than that it refuses everything.

The annotations come from ``Annotation.from_pg_row`` applied to a row captured out of the
real h database (``fixtures/pdf_annotation_row.json``), so the states under test are the
states the real column mapping produces from real column values, not a hand-shaped model
built to match the assertion.
"""

import json
import pathlib
from datetime import datetime
from typing import Any

import pytest
from click.testing import CliRunner
from conftest import close_the_session, free_port

from annotate.api import HClient
from annotate.cli import App, main
from annotate.models import Annotation
from annotate.session import write_open_time

CAPTURED_ROW = pathlib.Path(__file__).parent / "fixtures" / "pdf_annotation_row.json"

#: Every state in which h holds no usable normalized quote for a highlighted annotation.
UNUSABLE = [None, "", "  \n\t "]
UNUSABLE_IDS = ["no-normalized-row", "empty", "whitespace-only"]

#: What h stores when normalization worked: the LaTeX it recovered for the highlight.
RECOVERED = r"\mathcal{F}\mathcal{E}_{n,2}"

OPEN_TIME = datetime(2026, 7, 20, 12, 0, 0)
IN_SESSION = datetime(2026, 7, 20, 12, 0, 1)
#: ``wait`` parks the open timestamp at the real clock, so its batch has to be later than it.
AFTER_ANY_CLOCK = datetime(2099, 1, 1, 0, 0, 1)


def _annotation(normalized_quote: str | None, created: datetime) -> Annotation:
    """The captured h row, in the configured group, with ``normalized_quote`` as the value
    h's normalization left behind, mapped through the real production mapping."""
    row: dict[str, Any] = json.loads(CAPTURED_ROW.read_text())
    row["groupid"] = "grp"
    row["created"] = created
    row["normalized_quote"] = normalized_quote
    return Annotation.from_pg_row(row)


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
    """Records the ``acted`` tag writes a command made against h."""

    def __init__(self) -> None:
        self.tagged: list[tuple[str, list[str]]] = []

    def tag(self, annotation_id: str, add: list[str]) -> None:
        self.tagged.append((annotation_id, list(add)))


def _app(anns: list[Annotation], client: HClient | None = None) -> App:
    return App(source=_StubSource(anns), client=client or _StubClient(), group_id="grp")


def _ledger_ids(repo: pathlib.Path) -> list[str]:
    path = repo / "feedback" / "ledger.jsonl"
    return [json.loads(line)["id"] for line in path.read_text().splitlines()] if path.exists() else []


def _delivered_quotes(stdout: str) -> list[str]:
    """The quotes a command actually handed to the agent on stdout."""
    return [a["quote"] for a in json.loads(stdout)] if stdout.strip() else []


@pytest.mark.parametrize("normalized_quote", UNUSABLE, ids=UNUSABLE_IDS)
def test_pull_refuses_to_deliver_an_annotation_with_no_usable_normalized_quote(
    git_repo: pathlib.Path, normalized_quote: str | None
) -> None:
    write_open_time(git_repo, OPEN_TIME)

    result = CliRunner().invoke(main, ["pull"], obj=_app([_annotation(normalized_quote, IN_SESSION)]))

    assert result.exit_code != 0
    assert _delivered_quotes(result.stdout) == []
    assert _ledger_ids(git_repo) == []


@pytest.mark.parametrize("normalized_quote", UNUSABLE, ids=UNUSABLE_IDS)
def test_wait_refuses_to_deliver_an_annotation_with_no_usable_normalized_quote(
    git_repo: pathlib.Path, normalized_quote: str | None
) -> None:
    port = free_port()
    statuses: list[int] = []
    closer = close_the_session(port, statuses)

    result = CliRunner().invoke(
        main,
        ["wait", "--timeout", "20", "--port", str(port)],
        obj=_app([_annotation(normalized_quote, AFTER_ANY_CLOCK)]),
    )
    closer.join(timeout=5)

    assert statuses == [204]  # the browser really closed the session: wait reached delivery
    assert result.exit_code != 0
    assert _delivered_quotes(result.stdout) == []
    assert _ledger_ids(git_repo) == []


@pytest.mark.parametrize("normalized_quote", UNUSABLE, ids=UNUSABLE_IDS)
def test_record_refuses_to_append_an_annotation_with_no_usable_normalized_quote(
    git_repo: pathlib.Path, normalized_quote: str | None
) -> None:
    ann = _annotation(normalized_quote, IN_SESSION)

    result = CliRunner().invoke(main, ["record", ann.id], obj=_app([ann]))

    assert result.exit_code != 0
    assert result.stdout.strip() == ""
    assert _ledger_ids(git_repo) == []


@pytest.mark.parametrize("normalized_quote", UNUSABLE, ids=UNUSABLE_IDS)
def test_resolve_refuses_to_write_back_an_annotation_with_no_usable_normalized_quote(
    git_repo: pathlib.Path, normalized_quote: str | None
) -> None:
    write_open_time(git_repo, OPEN_TIME)
    client = _StubClient()

    result = CliRunner().invoke(main, ["resolve"], obj=_app([_annotation(normalized_quote, IN_SESSION)], client=client))

    assert result.exit_code != 0
    assert client.tagged == []  # nothing was marked acted in h


def test_pull_delivers_a_normalized_annotation(git_repo: pathlib.Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    ann = _annotation(RECOVERED, IN_SESSION)

    result = CliRunner().invoke(main, ["pull"], obj=_app([ann]))

    assert result.exit_code == 0, result.output
    assert _delivered_quotes(result.stdout) == [RECOVERED]
    assert _ledger_ids(git_repo) == [ann.id]


def test_wait_delivers_a_normalized_annotation(git_repo: pathlib.Path) -> None:
    port = free_port()
    statuses: list[int] = []
    closer = close_the_session(port, statuses)
    ann = _annotation(RECOVERED, AFTER_ANY_CLOCK)

    result = CliRunner().invoke(main, ["wait", "--timeout", "20", "--port", str(port)], obj=_app([ann]))
    closer.join(timeout=5)

    assert result.exit_code == 0, result.output
    assert _delivered_quotes(result.stdout) == [RECOVERED]
    assert _ledger_ids(git_repo) == [ann.id]


def test_record_appends_a_normalized_annotation(git_repo: pathlib.Path) -> None:
    ann = _annotation(RECOVERED, IN_SESSION)

    result = CliRunner().invoke(main, ["record", ann.id], obj=_app([ann]))

    assert result.exit_code == 0, result.output
    assert [e["quote"] for e in json.loads(result.stdout)] == [RECOVERED]
    assert _ledger_ids(git_repo) == [ann.id]


def test_resolve_writes_back_a_normalized_annotation(git_repo: pathlib.Path) -> None:
    write_open_time(git_repo, OPEN_TIME)
    ann = _annotation(RECOVERED, IN_SESSION)
    client = _StubClient()

    result = CliRunner().invoke(main, ["resolve"], obj=_app([ann], client=client))

    assert result.exit_code == 0, result.output
    assert client.tagged == [(ann.id, ["acted"])]
