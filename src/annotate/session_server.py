"""Loopback HTTP boundary used by the browser extension to close a review session."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)


class _CloseServer(HTTPServer):
    """Loopback server whose ``closed`` flag the handler flips on a close request.

    The flag lives on the server instance (one per ``wait_for_close`` call), not on the
    handler class: handlers are constructed per request, and class-level state would be
    shared between concurrent or successive servers.
    """

    closed = False


class _CloseHandler(BaseHTTPRequestHandler):
    server: _CloseServer

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib dispatch name
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib dispatch name
        if self.path != "/session/close":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.server.closed = True
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        # Route the stdlib server's request/error lines through logging instead of
        # letting them interleave with the CLI's stdout protocol -- but never discard
        # them: a rejected or malformed close request must leave a trace.
        log.info("session-close server: %s", format % args)


def wait_for_close(timeout: float, port: int = 8902) -> bool:
    """Serve the session-close endpoint until called or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    with _CloseServer(("127.0.0.1", port), _CloseHandler) as server:
        while not server.closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            server.timeout = min(remaining, 0.1)
            server.handle_request()
    return True
