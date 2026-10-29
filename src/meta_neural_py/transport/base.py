import uuid
from typing import Protocol

from ..models import Device


def bluetooth_uuid(value: str) -> str:
    """Normalize short and expanded Bluetooth-base UUIDs for comparison."""
    value = value.upper()
    if len(value) in (4, 8):
        value = value.zfill(8) + "-0000-1000-8000-00805F9B34FB"
    return str(uuid.UUID(value)).upper()


class Transport(Protocol):
    async def connect(self, device_id: str, timeout: float) -> None: ...
    async def read(self) -> bytes: ...
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


class Discovery(Protocol):
    async def discover(self, timeout: float) -> list[Device]: ...
