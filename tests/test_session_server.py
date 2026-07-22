import socket
import threading
from typing import cast

import httpx

from annotate.session_server import wait_for_close


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


def test_wait_for_close_returns_after_real_loopback_request() -> None:
    port = _free_port()
    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(wait_for_close(timeout=2, port=port)),
        daemon=True,
    )
    waiter.start()

    with httpx.Client() as client:
        for _attempt in range(20):
            try:
                response = client.post(f"http://127.0.0.1:{port}/session/close")
                break
            except httpx.ConnectError:
                threading.Event().wait(0.01)
        else:
            raise AssertionError("session close server did not start")

    waiter.join(timeout=2)

    assert response.status_code == 204
    assert result == [True]


def test_wait_for_close_times_out_without_a_close_request() -> None:
    assert wait_for_close(timeout=0.01, port=_free_port()) is False


def test_a_post_to_an_unknown_path_is_rejected_and_does_not_close_the_session() -> None:
    port = _free_port()
    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(wait_for_close(timeout=0.6, port=port)),
        daemon=True,
    )
    waiter.start()

    with httpx.Client() as client:
        for _attempt in range(20):
            try:
                response = client.post(f"http://127.0.0.1:{port}/not/the/endpoint")
                break
            except httpx.ConnectError:
                threading.Event().wait(0.01)
        else:
            raise AssertionError("session close server did not start")

    waiter.join(timeout=2)

    assert response.status_code == 404
    assert result == [False]  # the bogus request did not close the session
