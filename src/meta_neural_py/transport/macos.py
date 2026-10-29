"""Async IPC facade. Native Cocoa objects exist only in the managed helper."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

from ..errors import (
    BandError,
    DeviceDisconnected,
    LoginCancelled,
    PermissionDenied,
    ProtocolError,
    QueueOverflow,
    RequestTimeout,
    UnsupportedPlatform,
)
from ..models import Device

IPC_VERSION = 1
IPC_LIMIT = 131072


class Helper:
    def __init__(self, mode: str):
        self.mode = mode
        self.process: asyncio.subprocess.Process | None = None

    async def __aenter__(self):
        if sys.platform != "darwin":
            raise UnsupportedPlatform(
                "Native band access currently requires macOS; offline protocol/replay is portable"
            )
        env = dict(os.environ)
        # Module invocation works from an installed wheel and source/editable installs.
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "meta_neural_py.transport._native",
            self.mode,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=IPC_LIMIT,
        )
        try:
            async with asyncio.timeout(15):
                ready = await self.receive()
            if ready.get("event") != "hello" or ready.get("version") != IPC_VERSION:
                raise ProtocolError("Native helper IPC version mismatch")
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args):
        await self.close()

    async def send(self, message: dict):
        if self.process is None or self.process.stdin is None:
            raise DeviceDisconnected("Native helper is not running")
        line = (
            json.dumps({"version": IPC_VERSION, **message}, separators=(",", ":")).encode() + b"\n"
        )
        if len(line) > IPC_LIMIT:
            raise ProtocolError("Native command exceeded IPC limit")
        try:
            self.process.stdin.write(line)
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise DeviceDisconnected("Native helper stopped") from None

    async def receive(self) -> dict:
        if self.process is None or self.process.stdout is None:
            raise DeviceDisconnected("Native helper is not running")
        try:
            line = await self.process.stdout.readline()
            if not line:
                raise DeviceDisconnected("Native helper exited")
            value = json.loads(line)
            if not isinstance(value, dict) or value.get("version") != IPC_VERSION:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid native IPC response") from None
        if value.get("event") == "error":
            cls = {
                "permission": PermissionDenied,
                "cancelled": LoginCancelled,
                "overflow": QueueOverflow,
                "dependency": UnsupportedPlatform,
                "unsupported": UnsupportedPlatform,
                "transport": DeviceDisconnected,
            }.get(str(value.get("kind")), BandError)
            # Worker messages are fixed strings; never native error descriptions.
            raise cls(value.get("message", "Native helper failed"))
        return value

    async def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            async with asyncio.timeout(2):
                await process.wait()
        except TimeoutError:
            if process.returncode is None:
                process.terminate()
            try:
                async with asyncio.timeout(2):
                    await process.wait()
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()


class MacOSTransport:
    def __init__(self):
        self._helper: Helper | None = None
        self.battery_level: int | None = None

    async def discover(self, timeout: float) -> list[Device]:
        async with Helper("bluetooth") as helper:
            await helper.send({"op": "scan", "timeout": timeout})
            try:
                async with asyncio.timeout(timeout + 15):
                    response = await helper.receive()
            except TimeoutError:
                raise RequestTimeout(
                    "Bluetooth discovery timed out", operation="discover"
                ) from None
            if response.get("event") != "devices":
                raise ProtocolError("Unexpected scan response")
            return [Device(**d) for d in response["devices"]]

    async def connect(self, device_id: str, timeout: float):
        self._helper = await Helper("bluetooth").__aenter__()
        try:
            await self._helper.send({"op": "connect", "id": device_id, "timeout": timeout})
            async with asyncio.timeout(timeout):
                while True:
                    response = await self._helper.receive()
                    if response.get("event") == "connected":
                        return
        except TimeoutError:
            await self.close()
            raise RequestTimeout(
                "Bluetooth pairing/L2CAP deadline expired; accept the system pairing prompt",
                operation="connect",
                stage="pairing",
            ) from None
        except BaseException:
            await self.close()
            raise

    async def read(self) -> bytes:
        if self._helper is None:
            raise DeviceDisconnected("Transport is closed")
        while True:
            response = await self._helper.receive()
            event = response.get("event")
            if event == "data":
                try:
                    data = base64.b64decode(response["data"], validate=True)
                except (KeyError, ValueError):
                    raise ProtocolError("Invalid native data encoding") from None
                if not data or len(data) > 65536:
                    raise ProtocolError("Invalid native read size")
                return data
            if event == "battery":
                level = response.get("level")
                if type(level) is int and 0 <= level <= 100:
                    self.battery_level = level
            if event == "disconnected":
                raise DeviceDisconnected("Band disconnected")

    async def write(self, data: bytes):
        if self._helper is None:
            raise DeviceDisconnected("Transport is closed")
        for i in range(0, len(data), 32768):
            await self._helper.send(
                {"op": "write", "data": base64.b64encode(data[i : i + 32768]).decode()}
            )

    async def close(self):
        helper, self._helper = self._helper, None
        if helper is not None:
            await helper.close()
