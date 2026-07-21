import socket
import threading

import httpx

from annotate.session_server import wait_for_close


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_wait_for_close_returns_after_real_loopback_request():
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


def test_wait_for_close_times_out_without_a_close_request():
    assert wait_for_close(timeout=0.01, port=_free_port()) is False
