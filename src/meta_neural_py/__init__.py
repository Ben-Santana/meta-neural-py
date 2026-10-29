"""Experimental direct band access. Importing does not initialize Bluetooth."""

from .band import Band, Subscription
from .identity.store import Identity, IdentityStore, MemoryIdentityStore
from .models import (
    Battery,
    ClockDomain,
    ConnectionState,
    Device,
    DeviceInfo,
    Discontinuity,
    EMGBatch,
    EMGConfiguration,
    Event,
    Gesture,
    Gyroscope,
    Orientation,
    ReconnectPolicy,
    SensorEvent,
    Stats,
    Streams,
    Timeouts,
)

__version__ = "0.1.0a1"
__all__ = [
    "Band",
    "Subscription",
    "Battery",
    "ClockDomain",
    "ConnectionState",
    "Device",
    "DeviceInfo",
    "Discontinuity",
    "EMGBatch",
    "EMGConfiguration",
    "Event",
    "Gesture",
    "Gyroscope",
    "Orientation",
    "ReconnectPolicy",
    "SensorEvent",
    "Stats",
    "Streams",
    "Timeouts",
    "Identity",
    "IdentityStore",
    "MemoryIdentityStore",
]
