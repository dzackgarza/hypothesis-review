"""The whole of Send to agent, over the real stack, with nothing stubbed.

Every other test of the delivery path swaps in a stub source: they prove the CLI records
what it is handed. This one proves what a reader is actually handed. A session is opened
in a real repo, annotations are created through the live h API exactly as the browser
extension creates them -- over a page served here, so the backend really fetches and reads
it -- and ``annotate pull`` then runs for real, against real Postgres and real git.

What it holds the loop to is the reason the loop exists: the agent receives the
mathematics the reader highlighted, as the TeX its author wrote, and never the flattened
glyphs the browser captured. The page is shaped like the mathematical web the reader
spends their day on -- MathJax typesetting delimited TeX written straight into the text,
which is what Stack Exchange, MathOverflow and most course pages serve.

These need the live h stack (API + Postgres). It is the tool's real boundary: without it
the tool cannot function, so they fail loudly rather than skip, and they are collected
only under ``ANNOTATE_PG_IT=1`` (see ``tests/conftest.py``).
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
from annotate.cli import App, _now, main
from annotate.config import Config
from annotate.session import write_open_time
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
def test_the_agent_receives_the_mathematics_the_reader_highlighted(git_repo: Path, served_page: str, tagged: str) -> None:
    cfg = Config.load()
    write_open_time(git_repo, _now())  # the session the browser opens, on h's clock
    _annotate(cfg, served_page, tagged, "this is the definition I meant")

    result = CliRunner().invoke(main, ["pull"], obj=_live_app(cfg))

    assert result.exit_code == 0, result.output
    [delivered] = [a for a in json.loads(result.stdout) if tagged in a["tags"]]
    assert delivered["quote"] == AUTHORED
    assert delivered["text"] == "this is the definition I meant"
    # The ledger the agent's work is anchored to holds the same thing, committed.
    [recorded] = [json.loads(line) for line in (git_repo / "feedback" / "ledger.jsonl").read_text().splitlines() if json.loads(line)["id"] == delivered["id"]]
    assert recorded["quote"] == AUTHORED


@pytest.mark.pg
def test_the_agent_never_receives_the_browser_s_flattened_capture(git_repo: Path, served_page: str, tagged: str) -> None:
    # The failure this whole path exists to prevent: an agent acting on "2KZ∼0", which is
    # not mathematics anyone can read, act on, or paste back into a document.
    cfg = Config.load()
    write_open_time(git_repo, _now())
    _annotate(cfg, served_page, tagged, "note")

    result = CliRunner().invoke(main, ["pull"], obj=_live_app(cfg))

    [delivered] = [a for a in json.loads(result.stdout) if tagged in a["tags"]]
    assert delivered["quote"] != CAPTURED
    assert "2KZ∼0" not in delivered["quote"]


@pytest.mark.pg
def test_a_whole_session_of_notes_arrives_as_one_batch(git_repo: Path, served_page: str, tagged: str) -> None:
    # A reader works through a page and presses Send to agent once: everything they wrote
    # during the session arrives together, in the order they wrote it, each with its own
    # recovered quote.
    cfg = Config.load()
    write_open_time(git_repo, _now())
    _annotate(cfg, served_page, tagged, "first", exact=CAPTURED)
    _annotate(cfg, served_page, tagged, "second", exact="Its universal cover is a K3 surface X")

    result = CliRunner().invoke(main, ["pull"], obj=_live_app(cfg))

    batch = [a for a in json.loads(result.stdout) if tagged in a["tags"]]
    assert [a["text"] for a in batch] == ["first", "second"]
    assert batch[0]["quote"] == AUTHORED
    assert batch[1]["quote"] == r"Its universal cover is a K3 surface $X$"


def _live_app(cfg: Config) -> App:
    """The CLI wired exactly as `annotate` wires it in production."""
    return App(
        source=PostgresSource(cfg.pg_dsn),
        client=HClient(cfg.api_url, cfg.token),
        group_id=cfg.group_id,
        cfg=cfg,
    )


def _annotate(cfg: Config, page: str, tag: str, text: str, exact: str = CAPTURED) -> str:
    """Create one annotation through the live h API, as the browser extension does."""
    response = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={
            "uri": page,
            "text": text,
            "tags": [tag],
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
