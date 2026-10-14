"""Auth unit tests: REST bearer gate + optional WebSocket key gate."""
import pytest

from app.auth import AuthError, check_bearer, check_ws_key

KEYS = frozenset({"test-key-1", "test-key-2"})


def test_bearer_valid():
    check_bearer("Bearer test-key-1", KEYS)  # must not raise


def test_bearer_case_insensitive_scheme():
    check_bearer("bearer test-key-2", KEYS)


def test_bearer_missing():
    with pytest.raises(AuthError):
        check_bearer(None, KEYS)


def test_bearer_malformed():
    with pytest.raises(AuthError):
        check_bearer("Token test-key-1", KEYS)
    with pytest.raises(AuthError):
        check_bearer("Bearer ", KEYS)


def test_bearer_unknown_key():
    with pytest.raises(AuthError):
        check_bearer("Bearer nope", KEYS)


def test_bearer_empty_allowlist_denies_all():
    # Fail closed: misconfigured server never opens up.
    with pytest.raises(AuthError):
        check_bearer("Bearer test-key-1", frozenset())


def test_ws_key_absent_allowed():
    # iOS opens the socket with no auth headers.
    assert check_ws_key(None, KEYS) is True


def test_ws_key_present_validated():
    assert check_ws_key("test-key-1", KEYS) is True
    assert check_ws_key("wrong", KEYS) is False
