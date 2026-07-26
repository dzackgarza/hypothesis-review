"""The queue and drain workflow over the real stack, with nothing substituted.

Annotations are created through the live h API, read through real Postgres, drained
through the CLI, written to a real git ledger, and reread from both persistence surfaces.

What it holds the loop to is the reason the loop exists: the agent receives the
mathematics the reader highlighted, as the TeX its author wrote, and never the flattened
glyphs the browser captured. The page is shaped like the mathematical web the reader
spends their day on -- MathJax typesetting delimited TeX written straight into the text,
which is what Stack Exchange, MathOverflow and most course pages serve.

These need the live h stack (API + Postgres). It is the tool's real boundary: without it
the tool cannot function, so they fail loudly rather than skip.
"""

from __future__ import annotations

import http.server
import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from click.testing import CliRunner

from annotate.api import HClient
from annotate.cli import AGENT_QUEUE, App, main
from annotate.config import Config
from annotate.models import _public_id
from annotate.source import PostgresSource

#: A page of the shape the mathematical web serves: the author's TeX between dollars in
#: the page's own text, with MathJax told to typeset it in the reader's browser.
PAGE = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>A note on Enriques surfaces</title>
<script>MathJax = {tex: {inlineMath: [["$", "$"], ["\\\\(", "\\\\)"]]}};</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
</head><body><main>
<p>An Enriques surface satisfies $2K_Z \\sim 0$ and $q(Z) = 0$, so it sits between the
rational surfaces and the K3 surfaces.</p>
<p>Its universal cover is a K3 surface $X$ with a fixed-point-free involution.</p>
</main></body></html>
"""

#: What the reader's browser hands the client for a drag over the first sentence: MathJax's
#: rendering of each formula, not the TeX behind it.
CAPTURED = "An Enriques surface satisfies 2KZ∼0 and q(Z)=0, so it sits between the"

#: ...and the TeX the page's author wrote, which is what the agent has to receive.
AUTHORED = r"An Enriques surface satisfies $2K_Z \sim 0$ and $q(Z) = 0$, so it sits between the"


@pytest.mark.pg
def test_the_agent_sees_only_the_active_queue(git_repo: Path, served_page: str, tagged: str) -> None:
    cfg = Config.load()
    queued_id = _annotate(
        cfg,
        served_page,
        tagged,
        "this is the definition I meant",
        extra_tags=[AGENT_QUEUE],
    )
    _annotate(cfg, served_page, tagged, "not queued")

    result = CliRunner().invoke(main, ["queue"], obj=_live_app(cfg))

    assert result.exit_code == 0, result.output
    [delivered] = json.loads(result.stdout)
    assert delivered["id"] == queued_id
    assert delivered["quote"] == AUTHORED
    assert delivered["text"] == "this is the definition I meant"


@pytest.mark.pg
def test_drain_records_each_remediation_then_removes_only_the_queue_flag(
    git_repo: Path,
    served_page: str,
    tagged: str,
) -> None:
    cfg = Config.load()
    first_id = _annotate(
        cfg,
        served_page,
        tagged,
        "first",
        extra_tags=[AGENT_QUEUE, "paper"],
    )
    second_id = _annotate(
        cfg,
        served_page,
        tagged,
        "second",
        exact="Its universal cover is a K3 surface X",
        extra_tags=[AGENT_QUEUE],
    )

    result = CliRunner().invoke(
        main,
        [
            "drain",
            "--item",
            first_id,
            "Corrected the canonical divisor formula.",
            "--item",
            second_id,
            "Added the missing covering involution argument.",
        ],
        obj=_live_app(cfg),
    )

    assert result.exit_code == 0, result.output
    recorded = [json.loads(line) for line in (git_repo / "feedback" / "ledger.jsonl").read_text().splitlines()]
    assert [(entry["id"], entry["remediation"]) for entry in recorded] == [
        (first_id, "Corrected the canonical divisor formula."),
        (second_id, "Added the missing covering involution argument."),
    ]
    rows = _annotation_tags(cfg, tagged)
    assert rows[first_id] == {tagged, "paper", "acted"}
    assert rows[second_id] == {tagged, "acted"}


@pytest.mark.pg
def test_drain_rejects_a_nonqueued_item_before_recording_or_tagging(
    git_repo: Path,
    served_page: str,
    tagged: str,
) -> None:
    cfg = Config.load()
    annotation_id = _annotate(cfg, served_page, tagged, "not queued")

    result = CliRunner().invoke(
        main,
        ["drain", "--item", annotation_id, "Should not be accepted."],
        obj=_live_app(cfg),
    )

    assert result.exit_code != 0
    assert not (git_repo / "feedback" / "ledger.jsonl").exists()
    assert _annotation_tags(cfg, tagged)[annotation_id] == {tagged}


def _live_app(cfg: Config) -> App:
    """The CLI wired exactly as `annotate` wires it in production."""
    return App(
        source=PostgresSource(cfg.pg_dsn),
        client=HClient(cfg.api_url, cfg.token),
        group_id=cfg.group_id,
        cfg=cfg,
    )


def _annotate(
    cfg: Config,
    page: str,
    tag: str,
    text: str,
    exact: str = CAPTURED,
    extra_tags: list[str] | None = None,
) -> str:
    """Create one annotation through the live h API, as the browser extension does."""
    response = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={
            "uri": page,
            "text": text,
            "tags": [tag, *(extra_tags or [])],
            "group": cfg.group_id,
            "target": [
                {
                    "source": page,
                    "selector": [{"type": "TextQuoteSelector", "exact": exact}],
                }
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    identifier: Any = response.json()["id"]
    return str(identifier)


def _annotation_tags(cfg: Config, run_tag: str) -> dict[str, set[str]]:
    """Public annotation ids and tags for this run, read from the owned database boundary."""
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tags FROM annotation WHERE tags @> ARRAY[%s]::text[] AND deleted = false",
            (run_tag,),
        )
        rows = cur.fetchall()
    return {_public_id(identifier): set(tags) for identifier, tags in rows}


@pytest.fixture
def tagged() -> Iterator[str]:
    """A per-run tag that isolates this run's annotations, hard-deleted afterwards."""
    tag = f"__annotate_it_{uuid.uuid4().hex}"
    yield tag
    cfg = Config.load()
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


@pytest.fixture
def served_page() -> Iterator[str]:
    """Serve the page over HTTP so the backend fetches it the way it fetches any page."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *_args: Any) -> None:
            """Keep test output quiet; assertions cover the behavior."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/enriques.html"
    server.shutdown()
    thread.join()
    server.server_close()
