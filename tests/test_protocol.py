"""Protocol frame builders: shape, version, sequencing fields."""
from app import protocol


def test_envelope_shape_and_version():
    f = protocol.envelope("user_message", "req-1", {"text": "hi"})
    assert f == {
        "version": 1,
        "type": "user_message",
        "requestId": "req-1",
        "payload": {"text": "hi"},
    }
    assert protocol.PROTOCOL_VERSION == 1


def test_envelope_default_payload():
    assert protocol.envelope("ping", "r")["payload"] == {}


def test_delta_carries_seq():
    f = protocol.assistant_delta("r", "Hel", 3)
    assert f["type"] == "assistant_delta"
    assert f["payload"] == {"delta": "Hel", "seq": 3}


def test_complete_defaults_tool_calls():
    f = protocol.assistant_complete("r", "Done")
    assert f["payload"] == {"fullText": "Done", "toolCalls": []}


def test_error_frame_fields():
    f = protocol.assistant_error("r", "RATE_LIMITED", "slow down", True)
    assert f["payload"] == {
        "code": "RATE_LIMITED",
        "message": "slow down",
        "retryable": True,
    }


def test_pong_frame():
    f = protocol.pong_frame("r", 12.5)
    assert f["type"] == "pong"
    assert f["payload"] == {"timestamp": 12.5}
