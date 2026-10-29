import math
import struct
from typing import TypedDict

from ..errors import ProtocolError, UnsupportedConfiguration
from ..models import (
    Battery,
    ClockDomain,
    EMGBatch,
    EMGConfiguration,
    Gesture,
    Gyroscope,
    Orientation,
    SensorEvent,
)
from .protobuf import Fields

SENSOR_KINDS = {0x0200020D, 0x0200020F, 0x02000212, 0x0200020A}


class _Common(TypedDict):
    sequence: int
    timestamp: int
    received_at: float


def emg_configuration(payload: bytes) -> EMGConfiguration:
    rpc = Fields(payload)
    if rpc.integer(2) != 1:
        raise UnsupportedConfiguration("EMG configuration rejected")
    emg = Fields(Fields(rpc.bytes(6)).bytes(42))
    result = EMGConfiguration(*(emg.integer(n) for n in (1, 2, 4, 5, 10)))
    if not result.supported:
        raise UnsupportedConfiguration(
            "Supported EMG layout is 2048 Hz, 8 channels, 16 bits, 16 samples, encoding 0"
        )
    return result


def decode(
    kind: int, payload: bytes, now: float, configuration: EMGConfiguration | None = None
) -> SensorEvent:
    fields = Fields(payload)
    common: _Common = {
        "sequence": fields.integer(1),
        "timestamp": fields.integer(2),
        "received_at": now,
    }
    if kind == 0x0200020D:
        synthetic = fields.integer(12, 0)
        if synthetic not in (0, 1):
            raise ProtocolError("Invalid gesture synthetic flag")
        return Gesture(
            **common,
            source_clock=ClockDomain.GESTURE,
            finger_code=fields.integer(3, 0),
            action_code=fields.integer(4, 0),
            derived_action_code=fields.integer(5, 0),
            synthetic=bool(synthetic),
        )
    if kind == 0x0200020F:
        return Gyroscope(
            **common,
            source_clock=ClockDomain.MOTION,
            values=struct.unpack("<hhh", fields.bytes(3, 6)),
        )
    if kind == 0x02000212:
        values = struct.unpack("<ffff", fields.bytes(3, 16))
        if not all(math.isfinite(v) for v in values):
            raise ProtocolError("Nonfinite orientation component")
        return Orientation(**common, source_clock=ClockDomain.MOTION, values=values)
    if kind == 0x0200020A:
        if configuration is None or not configuration.supported:
            raise UnsupportedConfiguration(
                "Read and validate EMG configuration before decoding batches"
            )
        values = struct.unpack("<128H", fields.bytes(3, 256))
        return EMGBatch(
            **common,
            source_clock=ClockDomain.EMG,
            payload=payload,
            values=tuple(tuple(values[i : i + 8]) for i in range(0, 128, 8)),
        )
    raise UnsupportedConfiguration("Unknown sensor type")


def battery_status(payload: bytes) -> Battery:
    rpc = Fields(payload)
    if rpc.integer(2) != 1:
        raise ProtocolError("Battery status unavailable")
    battery = Fields(Fields(rpc.bytes(3)).bytes(1))
    level, charging = battery.integer(1), battery.integer(2) if 2 in battery else None
    if level > 100 or charging not in (0, 1, None):
        raise ProtocolError("Invalid battery status")
    return Battery(level, None if charging is None else bool(charging))
