"""Opt-in versioned sensor-only JSONL. Never accepts protocol or identity objects."""

import asyncio
import base64
import json
import math
import time
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from .errors import ProtocolError
from .models import (
    ClockDomain,
    Discontinuity,
    EMGBatch,
    Gesture,
    Gyroscope,
    Orientation,
    SensorEvent,
)

SCHEMA = "meta-neural-py.sensor.v1"
KINDS = {c.__name__: c for c in (Gesture, Gyroscope, Orientation, EMGBatch, Discontinuity)}


def write_event(output: TextIO, event: SensorEvent | Discontinuity):
    if type(event).__name__ not in KINDS or type(event) not in KINDS.values():
        raise TypeError("Only typed sensor events and discontinuities can be recorded")
    values = asdict(event)
    if isinstance(event, EMGBatch):
        values["payload"] = base64.b64encode(event.payload).decode()
    output.write(
        json.dumps(
            {"schema": SCHEMA, "kind": type(event).__name__, "event": values},
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


async def record(events: AsyncIterable, path: str | Path):
    """Create a new recording; refuses to overwrite an existing file."""
    with Path(path).open("x", encoding="utf-8") as output:
        async for event in events:
            if isinstance(event, (SensorEvent, Discontinuity)):
                write_event(output, event)


def read_event(line: str):
    try:
        if len(line) > 65536:
            raise ValueError()
        record = json.loads(line)
        if record["schema"] != SCHEMA:
            raise ValueError()
        cls = KINDS[record["kind"]]
        values = record["event"]
        values["source_clock"] = ClockDomain(values["source_clock"])
        if not isinstance(values["received_at"], (int, float)) or not math.isfinite(
            values["received_at"]
        ):
            raise ValueError()
        if cls is not Discontinuity:
            for name in ("sequence", "timestamp"):
                if type(values[name]) is not int or not 0 <= values[name] < 1 << 64:
                    raise ValueError()
        if cls in (Gyroscope, Orientation):
            components = values["values"]
            if not isinstance(components, list) or len(components) != (
                3 if cls is Gyroscope else 4
            ):
                raise ValueError()
            if cls is Gyroscope:
                if any(type(n) is not int or not -32768 <= n <= 32767 for n in components):
                    raise ValueError()
            elif any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in components):
                raise ValueError()
            values["values"] = tuple(components)
        elif cls is EMGBatch:
            import struct

            from .protocol.protobuf import Fields

            values["payload"] = base64.b64decode(values["payload"], validate=True)
            f = Fields(values["payload"])
            samples = struct.unpack("<128H", f.bytes(3, 256))
            if (f.integer(1), f.integer(2)) != (values["sequence"], values["timestamp"]):
                raise ValueError()
            expected = tuple(tuple(samples[i : i + 8]) for i in range(0, 128, 8))
            if tuple(tuple(row) for row in values["values"]) != expected:
                raise ValueError()
            values["values"] = expected
        elif cls is Gesture:
            for name in ("finger_code", "action_code", "derived_action_code"):
                if type(values[name]) is not int or not 0 <= values[name] < 1 << 64:
                    raise ValueError()
            if type(values["synthetic"]) is not bool:
                raise ValueError()
        return cls(**values)
    except (KeyError, ValueError, TypeError, ProtocolError, OverflowError):
        raise ProtocolError("Invalid sensor recording or unsupported schema") from None


async def replay(path: str | Path, *, speed: float = 0) -> AsyncIterator:
    """speed=0 emits immediately. Positive speed scales recorded receive intervals.

    Device timestamps and original monotonic receive times remain unchanged;
    replaying in another process does not make the source clocks comparable.
    """
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("Replay speed must be finite and nonnegative")
    first = None
    started = time.monotonic()
    with Path(path).open(encoding="utf-8") as source:
        while True:
            line = source.readline(65537)
            if not line:
                break
            event = read_event(line)
            if first is None:
                first = event.received_at
            if speed:
                await asyncio.sleep(
                    max(0, (event.received_at - first) / speed - (time.monotonic() - started))
                )
            yield event
