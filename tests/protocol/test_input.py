import struct

import pytest

from meta_neural_py.errors import ProtocolError, UnsupportedConfiguration
from meta_neural_py.models import ClockDomain, EMGConfiguration
from meta_neural_py.protocol.input import decode, emg_configuration
from meta_neural_py.protocol.protobuf import field


def test_unknown_gesture_codes_and_separate_clock_domains():
    gesture = decode(
        0x0200020D,
        field(1, 4) + field(2, 123) + field(3, 901) + field(4, 902) + field(5, 903) + field(12, 1),
        1.5,
    )
    assert gesture.finger == "unknown:901" and gesture.action_code == 902
    assert gesture.derived_action == "unknown:903" and gesture.synthetic
    assert gesture.source_clock == ClockDomain.GESTURE and gesture.timestamp == 123
    gyro = decode(
        0x0200020F,
        field(1, 1) + field(2, 123) + field(3, struct.pack("<hhh", -32768, 0, 32767)),
        2.5,
    )
    assert gyro.values == (-32768, 0, 32767)
    assert gyro.source_clock != gesture.source_clock


def test_quaternion_order_and_nonfinite_validation():
    common = field(1, 1) + field(2, 2)
    event = decode(0x02000212, common + field(3, struct.pack("<ffff", 1, 2, 3, 4)), 0)
    assert event.values == (1, 2, 3, 4)
    with pytest.raises(ProtocolError):
        decode(0x02000212, common + field(3, struct.pack("<ffff", float("nan"), 0, 0, 0)), 0)


def test_emg_unsigned_shape_and_configuration_required():
    payload = field(1, 5) + field(2, 9) + field(3, struct.pack("<128H", *range(65408, 65536)))
    config = EMGConfiguration(2048, 8, 16, 16, 0)
    event = decode(0x0200020A, payload, 0, config)
    assert len(event.values) == 16 and len(event.values[0]) == 8
    assert event.values[15][7] == 65535
    for unsupported in (None, EMGConfiguration(1024, 8, 16, 16, 0)):
        with pytest.raises(UnsupportedConfiguration):
            decode(0x0200020A, payload, 0, unsupported)
    with pytest.raises(ProtocolError):
        decode(0x0200020A, field(1, 5) + field(2, 9) + field(3, bytes(255)), 0, config)


def test_config_rejects_unsupported_encoding():
    config = b"".join(field(n, v) for n, v in [(1, 2048), (2, 8), (4, 16), (5, 16), (10, 1)])
    with pytest.raises(UnsupportedConfiguration):
        emg_configuration(field(2, 1) + field(6, field(42, config)))
