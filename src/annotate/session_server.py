"""Loopback HTTP boundary used by the browser extension to close a review session."""

from __future__ import annotations

import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer


class _CloseHandler(BaseHTTPRequestHandler):
    closed = False

    def handle_options(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def handle_post(self) -> None:
        if self.path != "/session/close":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        type(self).closed = True
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def log_message(self, _format: str, *_args: object) -> None:
        return


setattr(_CloseHandler, "do_OPTIONS", _CloseHandler.handle_options)
setattr(_CloseHandler, "do_POST", _CloseHandler.handle_post)


def wait_for_close(timeout: float, port: int = 8902) -> bool:
    """Serve the session-close endpoint until called or ``timeout`` expires."""
    _CloseHandler.closed = False
    deadline = time.monotonic() + timeout
    with HTTPServer(("127.0.0.1", port), _CloseHandler) as server:
        while not _CloseHandler.closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            server.timeout = min(remaining, 0.1)
            server.handle_request()
    return True
