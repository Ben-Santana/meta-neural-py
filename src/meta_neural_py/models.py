from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class ClockDomain(StrEnum):
    GESTURE = "device_gesture_us"
    MOTION = "device_motion_us"
    EMG = "device_emg_us"
    HOST = "host_monotonic_seconds"


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    rssi: int | None = None


@dataclass(frozen=True)
class Streams:
    gestures: bool = True
    gyro: bool = False
    orientation: bool = False
    emg: bool = False

    def __post_init__(self):
        if not all(type(v) is bool for v in (self.gestures, self.gyro, self.orientation, self.emg)):
            raise TypeError("Stream flags must be booleans")

    def flags(self) -> dict[int, int]:
        return {
            2: int(self.emg),
            3: int(self.gestures),
            6: int(self.gyro),
            8: int(self.orientation),
        }

    @property
    def any(self) -> bool:
        return any(self.flags().values())


@dataclass(frozen=True, kw_only=True)
class Event:
    received_at: float
    source_clock: ClockDomain


@dataclass(frozen=True, kw_only=True)
class SensorEvent(Event):
    sequence: int
    timestamp: int


@dataclass(frozen=True, kw_only=True)
class Gesture(SensorEvent):
    finger_code: int
    action_code: int
    derived_action_code: int
    synthetic: bool

    @property
    def finger(self) -> str:
        return _label(self.finger_code, ("unknown", "thumb", "index", "middle", "notApplicable"))

    @property
    def action(self) -> str:
        return _label(self.action_code, ACTIONS)

    @property
    def derived_action(self) -> str:
        return _label(self.derived_action_code, DERIVED_ACTIONS)


ACTIONS = (
    "unknown",
    "press",
    "release",
    "tap",
    "doubletap",
    "click",
    "up",
    "down",
    "left",
    "right",
    "wake",
    "swipeIn",
    "swipeOut",
    "ia",
    "partialPress",
    "partialRelease",
    "partialClick",
    "partialUp",
    "partialDown",
    "partialLeft",
    "partialRight",
)
DERIVED_ACTIONS = (
    "unknown",
    "singleTap",
    "doubleTap",
    "buttonHold",
    "buttonRelease",
    "buttonUp",
    "buttonDown",
    "buttonLeft",
    "buttonRight",
    "buttonPress",
    "buttonHoldRelease",
)


def _label(code: int, labels: tuple[str, ...]) -> str:
    return labels[code] if 0 <= code < len(labels) else f"unknown:{code}"


@dataclass(frozen=True, kw_only=True)
class Gyroscope(SensorEvent):
    values: tuple[int, int, int]


@dataclass(frozen=True, kw_only=True)
class Orientation(SensorEvent):
    # Wire order w, x, y, z. Anatomical axes and coordinate convention uncalibrated.
    values: tuple[float, float, float, float]


@dataclass(frozen=True)
class EMGConfiguration:
    sample_rate: int
    channels: int
    adc_bits: int
    samples_per_batch: int
    encoding: int

    @property
    def supported(self) -> bool:
        return (
            self.sample_rate,
            self.channels,
            self.adc_bits,
            self.samples_per_batch,
            self.encoding,
        ) == (2048, 8, 16, 16, 0)


@dataclass(frozen=True, kw_only=True)
class EMGBatch(SensorEvent):
    values: tuple[tuple[int, ...], ...]
    payload: bytes = field(repr=False)

    def as_numpy(self):
        """Return a (16, 8) uint16 copy; requires the numpy extra."""
        import numpy as np

        return np.asarray(self.values, dtype=np.uint16)


@dataclass(frozen=True, kw_only=True)
class Discontinuity(Event):
    reason: str
    stream: str | None = None
    previous_sequence: int | None = None
    sequence: int | None = None
    missing: int | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectionState(Event):
    state: str


@dataclass(frozen=True)
class Battery:
    level: int
    charging: bool | None = None


@dataclass(frozen=True)
class DeviceInfo:
    # Firmware schema is not yet validated; retain checked protobuf byte fields.
    fields: dict[int, bytes | int | tuple[bytes | int, ...]]
    firmware: str | None = None
    model: str | None = None


@dataclass
class Stats:
    bytes_received: int = 0
    bytes_sent: int = 0
    authenticated_records: int = 0
    sensor_events: int = 0
    emg_samples: int = 0
    emg_bytes: int = 0
    queue_drops: int = 0
    queue_dropped_bytes: int = 0
    # Unknown until device counter semantics are validated per source stream.
    sequence_gaps: int | None = None
    sequence_discontinuities: int = 0
    reconnects: int = 0
    last_traffic_at: float | None = None
    last_sensor_at: float | None = None
    shutdown_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconnectPolicy:
    attempts: int = 0
    delay: float = 1.0

    def __post_init__(self):
        import math

        if type(self.attempts) is not int or self.attempts < 0:
            raise ValueError("Reconnect attempts must be a nonnegative integer")
        if not math.isfinite(self.delay) or self.delay < 0:
            raise ValueError("Reconnect delay must be finite and nonnegative")


@dataclass(frozen=True)
class Timeouts:
    discovery: float = 5.0
    pairing: float = 60.0
    trust: float = 10.0
    rpc: float = 8.0
    first_sensor: float = 15.0
    stop: float = 3.0
    enrollment: float = 180.0

    def __post_init__(self):
        import math

        if any(not math.isfinite(v) or v <= 0 for v in self.__dict__.values()):
            raise ValueError("Timeouts must be finite and positive")


OverflowPolicy = Literal["error", "drop_oldest", "drop_newest"]
