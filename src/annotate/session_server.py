"""Loopback HTTP boundary used by the browser extension to close a review session."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

#: The loopback port the browser extension posts its session-close request to. A protocol
#: constant shared with the extension -- one correct value, declared here so the CLI and
#: the proof recipe name it instead of each restating the number.
SESSION_CLOSE_PORT = 8902


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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib dispatch name
        if self.path != "/session/close":
            # A plain send_error carries no CORS headers, so the browser would report an
            # opaque cross-origin failure instead of the 404; the extension must be able
            # to distinguish "unknown endpoint" from "session closed".
            self.log_message("rejected POST to unknown path %s", self.path)
            self.send_response(HTTPStatus.NOT_FOUND)
            self._cors_headers()
            self.end_headers()
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


def wait_for_close(timeout: float, port: int) -> bool:
    """Serve the session-close endpoint until called or ``timeout`` expires.

    ``port`` is supplied by the caller: the bound socket is this function's whole observable
    effect, so a defaulted port would let a caller that meant a different one silently serve
    the wrong endpoint and time out.
    """
    deadline = time.monotonic() + timeout
    with _CloseServer(("127.0.0.1", port), _CloseHandler) as server:
        while not server.closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            server.timeout = min(remaining, 0.1)
            server.handle_request()
    return True
