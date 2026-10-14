"""RateLimiter unit tests: pure in-memory logic, no I/O."""
from app.ratelimit import RateLimiter


def test_rest_window():
    lim = RateLimiter(rest_per_min=2)
    assert lim.check_rest("1.1.1.1") is True
    assert lim.check_rest("1.1.1.1") is True
    assert lim.check_rest("1.1.1.1") is False
    # Other IPs unaffected.
    assert lim.check_rest("2.2.2.2") is True


def test_ws_connection_cap_and_release():
    lim = RateLimiter(ws_max_conn_per_ip=1)
    assert lim.check_ws_connect("1.1.1.1") is True
    assert lim.check_ws_connect("1.1.1.1") is False
    lim.release_ws("1.1.1.1")
    assert lim.check_ws_connect("1.1.1.1") is True
    lim.release_ws("1.1.1.1")
    lim.release_ws("1.1.1.1")  # never goes negative
    assert lim.check_ws_connect("1.1.1.1") is True


def test_ws_message_cap():
    lim = RateLimiter(ws_msg_per_min=1)
    assert lim.check_ws_message("1.1.1.1") is True
    assert lim.check_ws_message("1.1.1.1") is False
