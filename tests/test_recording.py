import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from meta_neural_py.errors import ProtocolError, UnsupportedPlatform
from meta_neural_py.models import ClockDomain, Gesture
from meta_neural_py.recording import read_event, replay, write_event
from meta_neural_py.transport.macos import Helper


def gesture():
    return Gesture(
        sequence=1,
        timestamp=999,
        received_at=1.5,
        source_clock=ClockDomain.GESTURE,
        finger_code=1234,
        action_code=5678,
        derived_action_code=0,
        synthetic=False,
    )


async def test_sensor_recording_round_trip_and_replay(tmp_path):
    output = StringIO()
    event = gesture()
    write_event(output, event)
    assert read_event(output.getvalue()) == event
    path = tmp_path / "recording.jsonl"
    path.write_text(output.getvalue())
    assert [e async for e in replay(path)] == [event]
    with pytest.raises(TypeError):
        write_event(output, {"session_material": "secret"})


def test_replay_rejects_invalid_schema_and_nonfinite_fields():
    output = StringIO()
    write_event(output, gesture())
    data = json.loads(output.getvalue())
    data["event"]["received_at"] = float("nan")
    with pytest.raises(ProtocolError):
        read_event(json.dumps(data))
    data["schema"] = "future.v99"
    with pytest.raises(ProtocolError):
        read_event(json.dumps(data))


async def test_unsupported_platform_is_lazy_and_actionable():
    with patch.object(sys, "platform", "linux"):
        with pytest.raises(UnsupportedPlatform):
            async with Helper("bluetooth"):
                pass
