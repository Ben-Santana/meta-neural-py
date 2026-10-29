"""Pure protocol engine. No I/O, clocks, Bluetooth, logging, or persistent storage."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import ec

from ..errors import (
    BandError,
    EnrollmentError,
    IdentityMismatch,
    ProtocolError,
    RequestTimeout,
    TrustVerificationFailed,
    result_error,
)
from ..identity.enrollment import OwnershipCeremony, Pair, PairCompletion, PairRequest
from ..identity.store import Identity
from ..identity.trust import digest, load_point, public_point, sign, verify
from ..models import DeviceInfo, EMGConfiguration, Streams, Timeouts
from .airshield import AirShieldCipher, AirShieldReceiver, derive_key
from .datax import DataXReceiver, Frame, frame
from .input import SENSOR_KINDS, decode
from .protobuf import Fields, field


@dataclass(frozen=True)
class Signal:
    name: str
    value: object = None
    request_id: int | None = None
    error: BandError | None = None


@dataclass(frozen=True)
class Pending:
    operation: str
    request_id: int
    deadline: float
    stream_flags: bytes | None = None


class Session:
    def __init__(
        self,
        identity: Identity | None = None,
        *,
        ceremony: OwnershipCeremony | None = None,
        timeouts: Timeouts | None = None,
    ):
        if (identity is None) == (ceremony is None):
            raise IdentityMismatch("Provide a saved identity or an explicit enrollment ceremony")
        self.identity, self.ceremony = identity, ceremony
        self.timeouts = timeouts or Timeouts()
        self.stage = "idle"
        self._deadline = float("inf")
        self._private = ec.generate_private_key(ec.SECP256R1())
        self._point = public_point(self._private.public_key())
        self._challenge, self._seed, self._iv = (
            secrets.token_bytes(16),
            secrets.token_bytes(32),
            secrets.token_bytes(16),
        )
        self._counter = secrets.randbits(32)
        self._peer_point: bytes | None = None
        self._peer_challenge: bytes | None = None
        self._peer_seed: bytes | None = None
        self._tx: AirShieldCipher | None = None
        self._rx: AirShieldReceiver | None = None
        self._setup = DataXReceiver()
        self._datax = DataXReceiver()
        self._types: dict[int, int] = {}
        self._host_trusted = self._band_trusted = self._end_sent = False
        self._pending: dict[tuple[int, int], Pending] = {}
        self._opened: set[int] = set()
        self._next_id = 0
        self.emg_configuration: EMGConfiguration | None = None
        self.active_streams = Streams(False)
        self.authenticated_records = 0
        self.band_verified = False

    def start(self, now: float) -> bytes:
        if self.stage != "idle":
            raise ProtocolError("Session already started")
        self.stage, self._deadline = "encryption", now + self.timeouts.trust
        return frame(
            0x8001,
            (0x81000005, 0x02000001),
            field(1, self._point)
            + field(2, self._challenge)
            + field(3, 0)
            + field(4, 31)
            + field(7, 16),
        )

    def encrypt(self, data: bytes) -> bytes:
        if self._tx is None:
            raise ProtocolError("Encryption is not established")
        # DataX frames can span several transport records.
        return b"".join(self._tx.encrypt(data[i : i + 4096]) for i in range(0, len(data), 4096))

    def tick(self, now: float) -> list[Signal]:
        if now >= self._deadline:
            raise RequestTimeout("Startup deadline expired", operation="connect", stage=self.stage)
        result = []
        for key, pending in list(self._pending.items()):
            if now >= pending.deadline:
                del self._pending[key]
                result.append(
                    Signal(
                        "reply",
                        pending.operation,
                        pending.request_id,
                        RequestTimeout(
                            "Band request timed out",
                            operation=pending.operation,
                            channel=key[0],
                            stage=self.stage,
                        ),
                    )
                )
        return result

    def feed(self, data: bytes, now: float) -> tuple[bytes, list[object]]:
        outgoing = bytearray()
        events: list[object] = list(self.tick(now))
        if self._rx is None:
            # Process one setup frame at a time. EnableEncryption can be coalesced
            # with the first encrypted record, which must not enter the clear parser.
            self._setup.pending.extend(data)
            data = b""
            while self._rx is None and len(self._setup.pending) >= 4:
                size = (int.from_bytes(self._setup.pending[:2], "big") & 0x7FFF) + 4
                if size < 8 or not self._setup.pending[0] & 0x80:
                    raise ProtocolError("Invalid clear encryption frame")
                if len(self._setup.pending) < size:
                    break
                clear_packet = bytes(self._setup.pending[:size])
                del self._setup.pending[:size]
                parser = DataXReceiver()
                parsed = parser.feed(clear_packet, padded=False)
                outgoing.extend(self._handshake(parsed[0], now))
            if self._rx is not None:
                data = bytes(self._setup.pending)
                self._setup.pending.clear()
        if self._rx is not None:
            for plain in self._rx.feed(data):
                self.authenticated_records += 1
                for packet in self._datax.feed(plain):
                    out, emitted = self._receive(packet, now)
                    outgoing.extend(out)
                    events.extend(emitted)
        return bytes(outgoing), events

    def _handshake(self, packet: Frame, now: float) -> bytes:
        if not packet.words:
            raise ProtocolError("Untyped encryption frame")
        f, kind = Fields(packet.payload), packet.words[-1]
        point = f.bytes(1, 64)
        peer = load_point(point)
        if kind == 0x02000001:
            if (
                packet.channel != 0x8001
                or self._peer_point is not None
                or f.integer(3) != 0
                or f.integer(4) != 3
            ):
                raise ProtocolError("Unsupported encryption request")
            self._peer_point, self._peer_challenge = point, f.bytes(2, 16)
            return frame(
                1,
                (0x02000002,),
                field(1, self._point)
                + field(2, self._seed)
                + field(3, self._iv)
                + field(4, self._counter)
                + field(5, 3),
            )
        if (
            kind != 0x02000002
            or packet.channel != 1
            or point != self._peer_point
            or self._peer_challenge is None
            or f.integer(5) != 3
        ):
            raise ProtocolError("Unexpected encryption response")
        self._peer_seed = f.bytes(2, 32)
        iv, counter = f.bytes(3, 16), f.integer(4)
        secret = self._private.exchange(ec.ECDH(), peer)
        self._tx = AirShieldCipher(
            derive_key(secret, self._peer_challenge, self._seed), self._iv, self._counter
        )
        self._rx = AirShieldReceiver(
            AirShieldCipher(derive_key(secret, self._challenge, self._peer_seed), iv, counter)
        )
        if self.ceremony is not None:
            self.stage, self._deadline = "ceremony", now + self.timeouts.enrollment
            return self.encrypt(self.ceremony.start())
        self.stage, self._deadline = "trust", now + self.timeouts.trust
        return self._enable_trust()

    def _enable_trust(self, *, opened: bool = False) -> bytes:
        if self.identity is None or self._peer_point is None or self._peer_challenge is None:
            raise ProtocolError("Trust transcript is incomplete")
        signature = sign(
            self.identity.private_key,
            digest(self._peer_challenge, self._peer_point, self._seed, self._point),
        )
        words = (0x02001000,) if opened else (0x81000024, 0x02001000)
        return self.encrypt(
            frame(
                0x8002,
                words,
                field(
                    1, hashlib.sha256(public_point(self.identity.private_key.public_key())).digest()
                )
                + field(2, signature),
            )
        )

    def resume_ceremony(self, completion: PairCompletion) -> bytes:
        if self.stage != "ceremony" or self.ceremony is None:
            raise EnrollmentError("No enrollment HTTP step is pending")
        return self.encrypt(self.ceremony.resume(completion))

    def _receive(self, packet: Frame, now: float) -> tuple[bytes, list[object]]:
        kind: int | None
        if packet.words:
            kind = packet.words[-1]
            if kind & 0xFF000000 == 0x03000000:
                self._types.pop(packet.channel, None)
                return self._result(packet, kind)
            if len(self._types) >= 1024 and packet.channel not in self._types:
                raise ProtocolError("Too many protocol channels")
            self._types[packet.channel] = kind
        else:
            kind = self._types.get(packet.channel)
        if kind is None:
            return b"", []
        if kind == 0x02001001 and self._band_trusted:
            raise TrustVerificationFailed("Duplicate band proof")
        if self.stage == "ceremony":
            if packet.channel & 0x7FFF != 2 or self.ceremony is None:
                return b"", []
            result = self.ceremony.receive(kind, packet.payload)
            if isinstance(result, bytes):
                return self.encrypt(result), []
            if isinstance(result, (PairRequest, Pair)):
                return b"", [Signal("http", result)]
            self.identity = result
            self.stage, self._deadline = "trust", now + self.timeouts.trust
            return self._enable_trust(opened=True), [Signal("enrolled", result)]
        if self.stage == "trust":
            if kind == 0x02001001 and packet.channel & 0x8000:
                if self.identity is None or self._peer_seed is None or self._peer_point is None:
                    raise TrustVerificationFailed("Missing band identity")
                signature = Fields(packet.payload).bytes(2, 64)
                if self.identity.band_public_key is not None:
                    verify(
                        self.identity.band_public_key,
                        signature,
                        digest(self._challenge, self._point, self._peer_seed, self._peer_point),
                    )
                    self.band_verified = True
                elif not self.identity.allow_missing_band_key:
                    raise TrustVerificationFailed(
                        "Band proof cannot be verified without a saved public key"
                    )
                self._band_trusted = True
                ack = self.encrypt(frame(packet.channel & 0x7FFF, (0x03001000,)))
                return ack + self._maybe_end(), []
            if kind == 0x02001000 and packet.channel == 0x8001 and self._end_sent:
                fields = Fields(packet.payload)
                if fields.integer(1) != 1:
                    raise ProtocolError("Invalid EndLinkSetup acknowledgement")
                fields.bytes(2, 16)
                self.stage, self._deadline = "device_info", now + self.timeouts.rpc
                _, out = self.request("device_info", 3, 3, b"", now, allow_setup=True)
                return out, []
            return b"", []
        if kind == 0x02000315 and packet.channel & 0x8000 == 0:
            fields = Fields(packet.payload)
            request_id = fields.integer(1)
            key = (packet.channel, request_id)
            pending = self._pending.pop(key, None)
            if pending is None:
                return b"", []
            if fields.integer(2) != 1:
                error = BandError(
                    "Band rejected RPC status",
                    operation=pending.operation,
                    stage=self.stage,
                    channel=packet.channel,
                    code=fields.integer(2),
                )
                if pending.operation == "device_info":
                    raise error
                return b"", [Signal("reply", None, request_id, error)]
            if pending.operation == "device_info":
                info_fields = Fields(fields.bytes(4))
                self.stage, self._deadline = "ready", float("inf")
                info = DeviceInfo(
                    {
                        n: values[0][1] if len(values) == 1 else tuple(v[1] for v in values)
                        for n, values in info_fields.values.items()
                    }
                )
                return b"", [Signal("ready", info)]
            if pending.stream_flags is not None:
                wanted = Fields(pending.stream_flags)
                accepted = Fields(fields.bytes(5))
                if any(accepted.integer(n) != wanted.integer(n) for n in (2, 3, 6, 8)):
                    error = ProtocolError(
                        "Band accepted stream flags differ from requested flags",
                        operation=pending.operation,
                        channel=packet.channel,
                    )
                    return b"", [Signal("reply", None, request_id, error)]
                # Apply the acknowledgement before decoding subsequent coalesced
                # sensor frames, so the first sample cannot race the async waiter.
                self.active_streams = Streams(
                    bool(wanted.integer(3)),
                    bool(wanted.integer(6)),
                    bool(wanted.integer(8)),
                    bool(wanted.integer(2)),
                )
            return b"", [Signal("reply", packet.payload, request_id)]
        if self.stage == "ready" and kind in SENSOR_KINDS and self.active_streams.any:
            wanted_sensors = {
                0x0200020D: self.active_streams.gestures,
                0x0200020F: self.active_streams.gyro,
                0x02000212: self.active_streams.orientation,
                0x0200020A: self.active_streams.emg,
            }
            if wanted_sensors[kind]:
                return b"", [decode(kind, packet.payload, now, self.emg_configuration)]
        return b"", []

    def _result(self, packet: Frame, kind: int) -> tuple[bytes, list[object]]:
        channel = packet.channel & 0x7FFF
        if self.stage == "trust" and channel == 2:
            if kind != 0x03001000:
                raise result_error(
                    kind, operation="mutual_trust", stage=self.stage, channel=channel
                )
            if self._host_trusted:
                raise TrustVerificationFailed("Duplicate host trust result")
            self._host_trusted = True
            return self._maybe_end(), []
        pending = [(key, p) for key, p in self._pending.items() if key[0] == channel]
        operation = pending[0][1].operation if pending else self.stage
        error = result_error(kind, operation=operation, stage=self.stage, channel=channel)
        if self.stage != "ready":
            raise error
        if pending:
            events: list[object] = []
            for key, p in pending:
                del self._pending[key]
                events.append(Signal("reply", None, p.request_id, error))
            return b"", events
        # A terminal result on an active sensor service must not become a later timeout.
        if channel in (5, 6, 7):
            raise error
        return b"", []

    def _maybe_end(self) -> bytes:
        if self._host_trusted and self._band_trusted and not self._end_sent:
            self._end_sent = True
            return self.encrypt(
                frame(0x8001, (0x02001000,), field(1, 1) + field(2, secrets.token_bytes(16)))
            )
        return b""

    def request(
        self,
        operation: str,
        channel: int,
        field_number: int,
        payload: bytes,
        now: float,
        *,
        timeout: float | None = None,
        allow_setup: bool = False,
    ) -> tuple[int, bytes]:
        if self.stage != "ready" and not allow_setup:
            raise ProtocolError("Wait for authenticated device info before issuing requests")
        if any(c == channel for c, _ in self._pending):
            raise ProtocolError(
                "Only one outstanding RPC per channel is supported", channel=channel
            )
        self._next_id += 1
        request_id = self._next_id
        words = () if channel in self._opened else (0x8100CE56, 0x02000314)
        if words:
            # Service reuse supersedes the old remote type on both directions.
            self._types.pop(channel, None)
            self._types.pop(channel | 0x8000, None)
        self._opened.add(channel)
        self._pending[(channel, request_id)] = Pending(
            operation,
            request_id,
            now + (timeout or self.timeouts.rpc),
            payload if operation in ("start_streams", "stop_streams") else None,
        )
        out = self.encrypt(
            frame(0x8000 | channel, words, field(1, request_id) + field(field_number, payload))
        )
        return request_id, out

    def abandon_request(self, request_id: int):
        for key in list(self._pending):
            if key[1] == request_id:
                del self._pending[key]
