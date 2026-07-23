"""Shared fixtures for CLI tests whose commands record into a git repo.

The recording commands (`pull`/`wait`/`record`) commit the ledger with ``--no-verify``,
so they work against a plain throwaway repo; ``git_repo`` also chdirs into it, since the
commands resolve the repo from the current working directory.
"""

import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def free_port() -> int:
    """A loopback port nothing is bound to."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def http_service(status: int) -> Iterator[str]:
    """A real HTTP service on loopback answering every request with ``status``.

    Not a stand-in for anything under test: it is a genuine server, so the probe under test
    makes a real request over a real socket and sees a real response. It exists because a
    deployment that answers but is not serving (502/503 from a proxy in front of a dead app,
    500 from a misconfigured one) is the state the probe has to get right, and the live
    stack cannot be put into that state on demand. Yields its base URL.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's dispatch name
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def close_the_session(port: int, statuses: list[int]) -> threading.Thread:
    """Post the browser extension's real session-close request as soon as ``wait`` serves it.

    Runs off-thread because ``wait`` blocks the invoking thread on the loopback server; the
    retry loop covers the gap between the caller starting this thread and the server binding.
    Records the response status so the caller can assert the endpoint -- not merely the
    return value of a substituted function -- is what released the command.
    """

    def post() -> None:
        with httpx.Client() as client:
            for _attempt in range(200):
                try:
                    statuses.append(client.post(f"http://127.0.0.1:{port}/session/close").status_code)
                    return
                except httpx.ConnectError:
                    threading.Event().wait(0.01)
            raise AssertionError(f"annotate wait never served the session-close endpoint on port {port}")

    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    return thread
