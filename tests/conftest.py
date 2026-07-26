"""Shared fixtures for CLI tests and live-boundary annotation tests.

The drain command commits the ledger with ``--no-verify``, so it works against a plain
throwaway repo; ``git_repo`` also changes into it because the command resolves the reviewed
repository from the current working directory.
"""

import functools
import subprocess
import threading
from collections.abc import Iterator
from http.server import (
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from annotate.config import Config

#: The live-boundary tests drive the real self-hosted stack: h's API and its Postgres.
#: That stack is not an optional extra -- delivering a batch of feedback to an agent is
#: what this tool does, and it cannot be proved against anything else. So these tests are
#: always collected, and a stack that is not there fails the run rather than quietly
#: reducing what was proved (hypothesis-review#17).
_LIVE_MARKERS = ("pg", "e2e")


def _unreachable() -> str | None:
    """Why the live stack cannot be used, or None if it can.

    Reported the way `annotate doctor` reports it, because the two answers need different
    responses: a stack that is not running gets started, a stack that is running and broken
    gets debugged. This is the one place that translates a transport failure into that
    sentence -- it ends the run, it does not continue with less.
    """
    cfg = Config.load()
    try:
        response = httpx.get(f"{cfg.api_url}/api/", timeout=5)
    except httpx.HTTPError as exc:
        return f"nothing is serving h at {cfg.api_url} ({exc.__class__.__name__})"
    if response.status_code != httpx.codes.OK:
        return f"h at {cfg.api_url} is serving but answered HTTP {response.status_code}"
    try:
        with psycopg.connect(cfg.pg_dsn, connect_timeout=5) as connection:
            connection.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        return f"h's Postgres is not usable ({str(exc).strip().splitlines()[0]})"
    return None


@pytest.fixture(scope="session")
def live_stack() -> None:
    """Fail the run, once, when the stack the live-boundary tests need is not usable."""
    reason = _unreachable()
    if reason is not None:
        pytest.fail(f"the live h stack is required and unusable: {reason}", pytrace=False)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every live-boundary test the fixture that proves its stack is there."""
    for item in items:
        if any(item.get_closest_marker(marker) for marker in _LIVE_MARKERS):
            # Only function items carry fixtures; `fixturenames` is not on the base Item.
            if isinstance(item, pytest.Function):
                item.fixturenames.append("live_stack")


def _git(repo: Path, *args: str) -> None:
    # core.hooksPath=/dev/null keeps this throwaway repo off the machine-wide commit gate.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repo with the working directory inside it."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("proj\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


def committed_at_head(repo: Path) -> str:
    """The file paths in the repo's most recent commit."""
    return subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class _QuietFileHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with its request logging silenced.

    The stdlib handler already implements file serving (its ``do_GET`` is library code); this
    only quiets the per-request stderr noise so it does not interleave with test output.
    """

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture(scope="session")
def framework_page() -> Iterator[str]:
    """Serve the frameworkmath render-test page on loopback for the whole test session.

    The live-boundary seeds create HTML annotations whose URI h fetches server-side (at
    intake) to recover the authored TeX from the page's ``<span class="math">`` markup. That
    page is a fixture these tests own, not a service under test -- so the test stands it up
    itself rather than assuming an externally-run harness is listening on a fixed port. h
    resolves the loopback URL because it runs on the same host. Yields the page's URL.
    """
    fixtures = Path(__file__).parent / "fixtures"
    handler = functools.partial(_QuietFileHandler, directory=str(fixtures))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/frameworkmath.html"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
