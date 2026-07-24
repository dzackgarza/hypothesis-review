"""Loopback HTTP boundary used by the browser extension to close a review session."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
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

    #: How many annotations the open session would deliver if it were closed now. Supplied
    #: by the caller, because the count is a query against h that this module has no
    #: business making, and answered afresh on every request so the reader watching the
    #: indicator sees their note arrive rather than a number cached at startup.
    count_queued: Callable[[], int]


class _CloseHandler(BaseHTTPRequestHandler):
    server: _CloseServer

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib dispatch name
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # stdlib dispatch name, not ours to choose
        """Answer the extension's status poll: a session is here, and this much is queued.

        Without it the browser can only discover that no session is listening by failing
        a send the reader has already committed to. The count is what closing right now
        would deliver, so a note that has landed is visible as a number going up.
        """
        if self.path != "/session/status":
            self.log_message("rejected GET to unknown path %s", self.path)
            self.send_response(HTTPStatus.NOT_FOUND)
            self._cors_headers()
            self.end_headers()
            return
        body = json.dumps({"listening": True, "queued": self.server.count_queued()}).encode()
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def wait_for_close(timeout: float | None, port: int, count_queued: Callable[[], int]) -> bool:
    """Serve the session endpoints until closed, or until ``timeout`` expires.

    ``timeout`` is optional: ``None`` blocks indefinitely -- annotation has no fixed length,
    and the caller owns when to stop (a SIGINT/SIGTERM breaks the 0.1s poll loop, so the wait
    is interruptible without a deadline). A finite value bounds the wait and returns ``False``
    on expiry.

    ``port`` is supplied by the caller: the bound socket is this function's whole observable
    effect, so a defaulted port would let a caller that meant a different one silently serve
    the wrong endpoint and time out. ``count_queued`` likewise: the status endpoint reports
    what this session would deliver, and only the caller knows how to count that.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    with _CloseServer(("127.0.0.1", port), _CloseHandler) as server:
        server.count_queued = count_queued
        while not server.closed:
            if deadline is None:
                # No bound: poll at the same cadence so a signal still breaks the loop.
                server.timeout = 0.1
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                server.timeout = min(remaining, 0.1)
            server.handle_request()
    return True
