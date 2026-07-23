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
        target=lambda: result.append(wait_for_close(timeout=2, port=port, count_queued=lambda: 0)),
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
    assert wait_for_close(timeout=0.01, port=_free_port(), count_queued=lambda: 0) is False


def test_the_status_endpoint_reports_the_queue_as_it_changes() -> None:
    # What the toolbar indicator reads. The count has to be answered per request: a
    # session that reported its depth once, at startup, would show 0 forever while the
    # reader writes notes into it, which is worse than showing nothing.
    port = _free_port()
    queued = [0]
    waiter = threading.Thread(
        target=lambda: wait_for_close(timeout=2, port=port, count_queued=lambda: queued[0]),
        daemon=True,
    )
    waiter.start()

    with httpx.Client() as client:
        for _attempt in range(20):
            try:
                empty = client.get(f"http://127.0.0.1:{port}/session/status")
                break
            except httpx.ConnectError:
                threading.Event().wait(0.01)
        else:
            raise AssertionError("session server did not start")

        queued[0] = 3  # a note lands while the session is open
        filled = client.get(f"http://127.0.0.1:{port}/session/status")
        client.post(f"http://127.0.0.1:{port}/session/close")

    waiter.join(timeout=2)

    assert empty.json() == {"listening": True, "queued": 0}
    assert filled.json() == {"listening": True, "queued": 3}


def test_a_get_to_an_unknown_path_is_rejected() -> None:
    port = _free_port()
    waiter = threading.Thread(
        target=lambda: wait_for_close(timeout=0.6, port=port, count_queued=lambda: 0),
        daemon=True,
    )
    waiter.start()

    with httpx.Client() as client:
        for _attempt in range(20):
            try:
                response = client.get(f"http://127.0.0.1:{port}/not/the/endpoint")
                break
            except httpx.ConnectError:
                threading.Event().wait(0.01)
        else:
            raise AssertionError("session server did not start")

    waiter.join(timeout=2)

    assert response.status_code == 404


def test_a_post_to_an_unknown_path_is_rejected_and_does_not_close_the_session() -> None:
    port = _free_port()
    result: list[bool] = []
    waiter = threading.Thread(
        target=lambda: result.append(wait_for_close(timeout=0.6, port=port, count_queued=lambda: 0)),
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
