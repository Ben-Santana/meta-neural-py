from __future__ import annotations

import asyncio
import math
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from typing import Literal

from .errors import (
    BandError,
    DeviceBusy,
    DeviceDisconnected,
    EnrollmentError,
    IdentityMismatch,
    IdentityStoreError,
    ProtocolError,
    QueueOverflow,
    RequestTimeout,
    StreamShutdownError,
)
from .identity.enrollment import OwnershipCeremony, Pair, PairRequest
from .identity.keychain import KeychainIdentityStore
from .identity.meta_auth import MetaAuth
from .identity.meta_pair import MetaPairClient, PairClient
from .identity.store import Identity, IdentityStore, PendingEnrollment
from .identity.trust import load_point, public_point
from .models import (
    Battery,
    ClockDomain,
    Device,
    DeviceInfo,
    Discontinuity,
    EMGBatch,
    EMGConfiguration,
    Event,
    Gyroscope,
    Orientation,
    OverflowPolicy,
    ReconnectPolicy,
    SensorEvent,
    Stats,
    Streams,
    Timeouts,
)
from .protocol.input import battery_status, emg_configuration
from .protocol.protobuf import Fields, field
from .protocol.session import Session, Signal
from .transport.base import Discovery, Transport
from .transport.lock import DeviceLock
from .transport.macos import MacOSTransport


class Subscription:
    """One acknowledged stream owner with independently bounded consumer storage."""

    def __init__(
        self,
        band: Band,
        streams: Streams,
        *,
        queue_size: int = 256,
        overflow: OverflowPolicy = "error",
        queue_bytes: int = 1 << 20,
        queue_samples: int = 4096,
    ):
        if any(type(n) is not int or n <= 0 for n in (queue_size, queue_bytes, queue_samples)):
            raise ValueError("Queue budgets must be positive integers")
        if overflow not in ("error", "drop_oldest", "drop_newest"):
            raise ValueError("Invalid overflow policy")
        if not streams.any:
            raise ValueError("Request at least one stream")
        self.band, self.streams = band, streams
        self.queue_size, self.overflow = queue_size, overflow
        self.queue_bytes, self.queue_samples = queue_bytes, queue_samples
        self._queue: deque[tuple[Event, int, int]] = deque()
        self._bytes = self._samples = 0
        self._wake = asyncio.Event()
        self._error: BaseException | None = None
        self._closed = False
        self._entered = False
        self._sensor_started: float | None = None
        self._continuous_seen = False

    async def __aenter__(self) -> Subscription:
        if self._entered:
            raise RuntimeError("A subscription context cannot be entered twice")
        self.band._require_ready()
        if self.band._subscription is not None:
            raise DeviceBusy("Only one subscription per Band is supported")
        self._entered = True
        self.band._subscription = self
        try:
            await self.band._start_streams(self.streams)
            self._sensor_started = time.monotonic()
        except BaseException as error:
            await self.band._finish_subscription(self, error)
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.band._finish_subscription(self, exc)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        if not self._entered:
            raise RuntimeError("Enter the subscription context before iterating")
        while True:
            if self._error is not None:
                raise self._error
            if self._queue:
                event, size, samples = self._queue.popleft()
                self._bytes -= size
                self._samples -= samples
                return event
            if self._closed:
                raise StopAsyncIteration
            self._wake.clear()
            await self._wake.wait()

    def _drop(self, item):
        _, size, _ = item
        self.band.stats.queue_drops += 1
        self.band.stats.queue_dropped_bytes += size

    def _push(self, event: Event):
        if self._closed or self._error is not None:
            return
        size = len(event.payload) if isinstance(event, EMGBatch) else 128
        samples = 16 if isinstance(event, EMGBatch) else 0
        item = (event, size, samples)

        def fits():
            return (
                len(self._queue) < self.queue_size
                and self._bytes + size <= self.queue_bytes
                and self._samples + samples <= self.queue_samples
            )

        if not fits():
            if self.overflow == "error":
                self._drop(item)
                self._fail(
                    QueueOverflow(
                        "Consumer queue exceeded event/byte/sample budget; consume faster or choose a drop policy"
                    )
                )
                return
            if (
                self.overflow == "drop_newest"
                or size > self.queue_bytes
                or samples > self.queue_samples
            ):
                self._drop(item)
                return
            while self._queue and not fits():
                old = self._queue.popleft()
                self._bytes -= old[1]
                self._samples -= old[2]
                self._drop(old)
        self._queue.append(item)
        self._bytes += size
        self._samples += samples
        self._wake.set()

    def _fail(self, error: BaseException):
        if self._error is None:
            self._error = error
        self._wake.set()

    def _close(self):
        self._closed = True
        self._wake.set()


class Band:
    def __init__(
        self,
        device_id: str,
        *,
        identity_store: IdentityStore | None = None,
        reconnect: ReconnectPolicy | None = None,
        timeouts: Timeouts | None = None,
        transport_factory: Callable[[], Transport] | None = None,
        lock_directory=None,
    ):
        try:
            self.id = str(uuid.UUID(device_id)).upper()
        except (ValueError, AttributeError):
            raise ValueError("Select a discovered device UUID") from None
        self.identity_store = (
            identity_store if identity_store is not None else KeychainIdentityStore()
        )
        self.reconnect = reconnect or ReconnectPolicy()
        self.timeouts = timeouts or Timeouts()
        self._factory = transport_factory or MacOSTransport
        self._lock = DeviceLock(self.id, lock_directory)
        self._transport: Transport | None = None
        self._session: Session | None = None
        self._reader: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._replies: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._channel_locks: dict[int, asyncio.Lock] = {}
        self._hand_lock = asyncio.Lock()
        self._subscription: Subscription | None = None
        self._closing = False
        self._entered = False
        self._failure: BaseException | None = None
        self._ceremony: OwnershipCeremony | None = None
        self._pair_client: PairClient | None = None
        self._record_base = 0
        self._recovery: asyncio.Task | None = None
        self._ceremony_tasks: set[asyncio.Task] = set()
        self._previous: dict[type, tuple[int, int]] = {}
        self.device_info: DeviceInfo | None = None
        self.battery: Battery | None = None
        self.hand: Literal["left", "right"] | None = None
        self.active_streams = Streams(False)
        self.state = "disconnected"
        self.stats = Stats()

    @classmethod
    async def discover(
        cls, timeout: float = 5, *, transport: Discovery | None = None
    ) -> list[Device]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Discovery timeout must be finite and positive")
        return await (transport or MacOSTransport()).discover(timeout)

    @classmethod
    async def enroll(
        cls,
        device_id: str,
        *,
        auth: MetaAuth | None = None,
        identity_store: IdentityStore | None = None,
        pair_client: PairClient | None = None,
        band_public_key: bytes | None = None,
        allow_missing_band_key: bool = False,
        transport_factory: Callable[[], Transport] | None = None,
        timeouts: Timeouts | None = None,
        lock_directory=None,
    ) -> Identity:
        """Explicitly change ownership, then reconnect before committing the identity.

        Interrupted attempts retain a separate pending Keychain record. Enrollment
        never deletes or replaces a working identity until the new one is proven.
        """
        band = cls(
            device_id,
            identity_store=identity_store,
            transport_factory=transport_factory,
            timeouts=timeouts,
            lock_directory=lock_directory,
        )
        # Acquire ownership before interactive auth to serialize enrollment attempts.
        band._lock.acquire()
        try:
            async with asyncio.timeout(band.timeouts.enrollment):
                ceremony = OwnershipCeremony(
                    band_public_key, allow_missing_band_key=allow_missing_band_key
                )
                if pair_client is None:
                    session = await (auth or MetaAuth()).authenticate()
                    pair_client = MetaPairClient(session)
                await asyncio.to_thread(
                    band.identity_store.save_pending,
                    band.id,
                    PendingEnrollment(
                        ceremony.private_key, "identity", band_public_key, allow_missing_band_key
                    ),
                )
                band._ceremony, band._pair_client = ceremony, pair_client
                await band._open(ceremony=ceremony)
                pending = await asyncio.to_thread(band.identity_store.load_pending, band.id)
                if pending is None or pending.stage != "confirmed":
                    raise IdentityStoreError("Confirmed enrollment did not persist correctly")
                identity = Identity(
                    pending.private_key,
                    load_point(pending.band_point) if pending.band_point else None,
                    pending.allow_missing_band_key,
                )
                # A fresh encrypted connection proves that persisted enrollment works.
                await band._close_connection()
                band._ceremony = None
                await band._open(identity=identity)
                await asyncio.to_thread(band.identity_store.save, band.id, identity)
                saved = await asyncio.to_thread(band.identity_store.load, band.id)
                if (
                    saved is None
                    or public_point(saved.private_key.public_key())
                    != public_point(identity.private_key.public_key())
                    or (public_point(saved.band_public_key) if saved.band_public_key else None)
                    != (
                        public_point(identity.band_public_key) if identity.band_public_key else None
                    )
                    or saved.allow_missing_band_key != identity.allow_missing_band_key
                ):
                    raise IdentityStoreError("Verified identity did not persist correctly")
                await asyncio.to_thread(band.identity_store.clear_pending, band.id)
                return identity
        except TimeoutError:
            raise RequestTimeout(
                "Enrollment deadline expired; pending recovery state retained", operation="enroll"
            ) from None
        finally:
            try:
                await band._close_preserving(sys.exception())
            finally:
                band._lock.release()

    async def __aenter__(self) -> Band:
        if self._entered:
            raise RuntimeError("Band context is already entered")
        self._lock.acquire()
        self._entered = True
        try:
            identity = await asyncio.to_thread(self.identity_store.load, self.id)
            if identity is None:
                pending = await asyncio.to_thread(self.identity_store.load_pending, self.id)
                suffix = f" Interrupted enrollment stage: {pending.stage}." if pending else ""
                raise IdentityMismatch(
                    "No verified identity saved; run Band.enroll explicitly." + suffix
                )
            await self._open(identity=identity)
            return self
        except BaseException as error:
            try:
                await self._close_preserving(error)
            finally:
                self._entered = False
                self._lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb):
        self._closing = True
        shutdown_error = None
        try:
            if self._subscription is not None:
                try:
                    await self._finish_subscription(self._subscription, exc)
                except BaseException as error:
                    shutdown_error = error
        finally:
            try:
                await self._close_preserving(exc or shutdown_error)
            finally:
                self._lock.release()
                self._entered = False
                self._closing = False
        if shutdown_error is not None:
            if exc is None:
                raise shutdown_error
            exc.add_note(f"Band shutdown failed: {shutdown_error}")

    def subscribe(
        self,
        streams: Streams,
        *,
        queue_size: int = 256,
        overflow: OverflowPolicy = "error",
        queue_bytes: int = 1 << 20,
        queue_samples: int = 4096,
    ) -> Subscription:
        return Subscription(
            self,
            streams,
            queue_size=queue_size,
            overflow=overflow,
            queue_bytes=queue_bytes,
            queue_samples=queue_samples,
        )

    @property
    def emg_configuration(self) -> EMGConfiguration | None:
        """The validated configuration for the current connection, when read."""
        return self._session.emg_configuration if self._session is not None else None

    @property
    def band_verified(self) -> bool:
        """Whether the current session verified the band's proof against its public key."""
        return self._session.band_verified if self._session is not None else False

    def _require_ready(self):
        if self._failure is not None:
            raise self._failure
        if self._session is None or self._session.stage != "ready":
            raise DeviceDisconnected("Enter an authenticated Band context first")

    async def _open(self, *, identity=None, ceremony=None):
        self._failure = None
        self.hand, self.battery, self.device_info = None, None, None
        self.state = "connecting"
        self._ready = asyncio.get_running_loop().create_future()
        self._transport = self._factory()
        try:
            await self._transport.connect(self.id, self.timeouts.pairing)
            self._session = Session(identity, ceremony=ceremony, timeouts=self.timeouts)
            self._reader = asyncio.create_task(self._read_loop(), name="meta-neural-reader")
            await self._send(self._session.start(time.monotonic()))
            # Stage-specific protocol deadlines are checked by the reader.
            await self._ready
        except BaseException:
            if self._ready is not None and self._ready.done() and not self._ready.cancelled():
                self._ready.exception()  # retrieve failures if the caller cancelled concurrently
            raise

    async def _send(self, data):
        if not data:
            return
        async with self._write_lock:
            if self._transport is None:
                raise DeviceDisconnected("Transport closed")
            await self._transport.write(data)
            self.stats.bytes_sent += len(data)

    async def _read_loop(self):
        read_task = None
        try:
            assert self._session is not None and self._transport is not None
            while True:
                if read_task is None:
                    read_task = asyncio.create_task(self._transport.read())
                ready, _ = await asyncio.wait({read_task}, timeout=0.05)
                now = time.monotonic()
                for signal in self._session.tick(now):
                    await self._dispatch(signal)
                if ready:
                    data = read_task.result()
                    read_task = None
                    if not data:
                        raise DeviceDisconnected("Band transport reached EOF")
                    self.stats.bytes_received += len(data)
                    self.stats.last_traffic_at = now
                    output, events = self._session.feed(data, now)
                    self.stats.authenticated_records = (
                        self._record_base + self._session.authenticated_records
                    )
                    await self._send(output)
                    for event in events:
                        await self._dispatch(event)
                    level = getattr(self._transport, "battery_level", None)
                    if self.battery is None and level is not None:
                        self.battery = Battery(level)
                sub = self._subscription
                if sub and sub._sensor_started is not None and not sub._continuous_seen:
                    if sub.streams.gyro or sub.streams.orientation or sub.streams.emg:
                        if now - sub._sensor_started >= self.timeouts.first_sensor:
                            sub._fail(
                                RequestTimeout(
                                    "Subscription acknowledged but no continuous sensor data arrived",
                                    operation="first_sensor",
                                    stage="streaming",
                                )
                            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._failure = error
            self.state = "disconnected"
            self._fail_replies(error)
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)
            elif self._subscription is not None:
                # Recovery has its own task; the reader never waits for its own RPCs.
                if (
                    isinstance(error, (DeviceDisconnected, OSError))
                    and self.reconnect.attempts
                    and not self._closing
                    and (self._recovery is None or self._recovery.done())
                ):
                    task = asyncio.create_task(self._recover(), name="meta-neural-reconnect")
                    self._recovery = task
                else:
                    if self._recovery is None or self._recovery.done():
                        self._subscription._fail(error)
        finally:
            if read_task is not None:
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task

    async def _dispatch(self, event):
        if isinstance(event, Signal):
            if event.name == "ready":
                assert isinstance(event.value, DeviceInfo)
                self.device_info = event.value
                self.state = "connected"
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(None)
            elif event.name == "reply":
                assert event.request_id is not None
                future = self._replies.pop(event.request_id, None)
                if future is not None and not future.done():
                    if event.error is not None:
                        future.set_exception(event.error)
                    else:
                        future.set_result(event.value)
            elif event.name == "http":
                task = asyncio.create_task(
                    self._perform_http(event.value), name="meta-neural-enrollment-http"
                )
                self._ceremony_tasks.add(task)
                task.add_done_callback(self._ceremony_tasks.discard)
            elif event.name == "enrolled":
                identity = event.value
                assert isinstance(identity, Identity)
                await asyncio.to_thread(
                    self.identity_store.save_pending,
                    self.id,
                    PendingEnrollment(
                        identity.private_key,
                        "confirmed",
                        public_point(identity.band_public_key)
                        if identity.band_public_key
                        else None,
                        identity.allow_missing_band_key,
                    ),
                )
            return
        if isinstance(event, SensorEvent):
            self.stats.sensor_events += 1
            self.stats.last_sensor_at = event.received_at
            if isinstance(event, EMGBatch):
                self.stats.emg_samples += 16
                self.stats.emg_bytes += len(event.payload)
            previous = self._previous.get(type(event))
            self._previous[type(event)] = (event.sequence, event.timestamp)
            if previous and (event.sequence != previous[0] + 1 or event.timestamp < previous[1]):
                increasing = event.sequence > previous[0] + 1 and event.timestamp >= previous[1]
                self.stats.sequence_discontinuities += 1
                if self._subscription is not None:
                    self._subscription._push(
                        Discontinuity(
                            received_at=event.received_at,
                            source_clock=event.source_clock,
                            reason="unvalidated_sequence_step"
                            if increasing
                            else "sequence_reset_or_wrap",
                            stream=type(event).__name__,
                            previous_sequence=previous[0],
                            sequence=event.sequence,
                            missing=None,
                        )
                    )
            if self._subscription is not None:
                if isinstance(event, (Gyroscope, Orientation, EMGBatch)):
                    self._subscription._continuous_seen = True
                self._subscription._push(event)

    async def _perform_http(self, request):
        try:
            assert (
                self._pair_client is not None
                and self._ceremony is not None
                and self._session is not None
            )
            if isinstance(request, PairRequest):
                result = await self._pair_client.pair_request(request)
                stage = "claim"
            elif isinstance(request, Pair):
                result = await self._pair_client.pair(request)
                stage = "finish"
            else:
                raise EnrollmentError("Unexpected ceremony HTTP step")
            await asyncio.to_thread(
                self.identity_store.save_pending,
                self.id,
                PendingEnrollment(
                    self._ceremony.private_key,
                    stage,
                    result.band_public_key or self._ceremony.expected_band_point,
                    self._ceremony.allow_missing_band_key,
                ),
            )
            await self._send(self._session.resume_ceremony(result))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._failure = error
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)

    def _fail_replies(self, error):
        for future in self._replies.values():
            if not future.done():
                future.set_exception(error)
        self._replies.clear()

    async def _rpc(self, operation, channel, field_number, payload=b"", *, timeout=None):
        self._require_ready()
        lock = self._channel_locks.setdefault(channel, asyncio.Lock())
        async with lock:
            self._require_ready()
            session = self._session
            assert session is not None
            request_id, out = session.request(
                operation, channel, field_number, payload, time.monotonic(), timeout=timeout
            )
            future = asyncio.get_running_loop().create_future()
            self._replies[request_id] = future
            try:
                await self._send(out)
                return await future
            finally:
                self._replies.pop(request_id, None)
                session.abandon_request(request_id)
                if future.done() and not future.cancelled():
                    future.exception()

    async def _start_streams(self, streams):
        self._previous.clear()
        assert self._session is not None
        # Establish the input subscription before updating it. Never enable EMG
        # until its independent configuration read has passed validation.
        await self._rpc("input_subscription", 5, 4)
        if streams.emg:
            payload = await self._rpc("emg_configuration", 7, 5)
            self._session.emg_configuration = emg_configuration(payload)
        payload = b"".join(field(n, v) for n, v in streams.flags().items())
        response = await self._rpc("start_streams", 5, 4, payload)
        self._verify_flags(response, streams)
        self.active_streams = self._session.active_streams = streams

    @staticmethod
    def _verify_flags(response, streams):
        flags = Fields(Fields(response).bytes(5))
        if any(flags.integer(n) != v for n, v in streams.flags().items()):
            raise ProtocolError(
                "Band accepted stream flags differ from requested flags", operation="stream_control"
            )

    async def _stop_streams(self):
        if self._session is None or self._session.stage != "ready" or self._failure is not None:
            raise StreamShutdownError("Cannot acknowledge stream shutdown after link failure")
        payload = b"".join(field(n, 0) for n in (2, 3, 6, 8))
        try:
            response = await self._rpc("stop_streams", 5, 4, payload, timeout=self.timeouts.stop)
            self._verify_flags(response, Streams(False))
        except Exception as error:
            raise StreamShutdownError("Band did not acknowledge all streams stopped") from error
        self.active_streams = self._session.active_streams = Streams(False)

    async def _finish_subscription(self, sub: Subscription, original_error: BaseException | None):
        if self._subscription is not sub:
            return
        recovery = self._recovery
        if recovery is not None and not recovery.done():
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
        error = None
        try:
            # Shield bounded shutdown from the cancellation that ended the consumer.
            task = asyncio.create_task(self._stop_streams())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        except BaseException as caught:
            error = caught
            self.stats.shutdown_errors.append(str(caught))
            if original_error is not None:
                original_error.add_note(f"Stream shutdown failed: {caught}")
        finally:
            self._subscription = None
            sub._close()
            if error is not None:
                # An unacknowledged stop must never leave native streams running.
                await self._close_preserving(original_error or error)
        if error is not None and original_error is None:
            raise error

    async def read_hand(self) -> Literal["left", "right"]:
        async with self._hand_lock:
            return await self._read_hand_request()

    async def _read_hand_request(self) -> Literal["left", "right"]:
        response = Fields(await self._rpc("read_hand", 6, 5))
        value = Fields(response.bytes(6)).integer(10)
        if value not in (0, 1):
            raise ProtocolError("Invalid hand configuration")
        self.hand = "left" if value else "right"
        return self.hand

    async def set_hand(self, hand: Literal["left", "right"]) -> Literal["left", "right"]:
        if hand not in ("left", "right"):
            raise ValueError("Hand must be left or right")
        async with self._hand_lock:
            await self._rpc("set_hand", 6, 5, field(10, int(hand == "left")))
            confirmed = await self._read_hand_request()
            if confirmed != hand:
                raise ProtocolError("Band did not retain the selected hand", operation="set_hand")
            return confirmed

    async def read_battery(self) -> Battery | None:
        try:
            response = await self._rpc("battery", 8, 2)
            self.battery = battery_status(response)
        except BandError:
            # Optional battery service failure is isolated from the sensor subscription.
            level = getattr(self._transport, "battery_level", None)
            self.battery = Battery(level) if level is not None else None
        return self.battery

    async def _recover(self):
        sub = self._subscription
        last_error = self._failure
        # Preserve one identity object; never invoke enrollment or reload a different key.
        assert self._session is not None
        identity = self._session.identity
        try:
            await self._close_connection(cancel_recovery=False)
        except Exception as error:
            self.stats.shutdown_errors.append(str(error))
            self._failure = error
            if sub is not None:
                sub._fail(error)
            return
        for _ in range(self.reconnect.attempts):
            if self._closing or self._subscription is not sub:
                return
            try:
                await asyncio.sleep(self.reconnect.delay)
                await self._open(identity=identity)
                self.stats.reconnects += 1
                self._previous.clear()
                if sub is not None:
                    sub._push(
                        Discontinuity(
                            received_at=time.monotonic(),
                            source_clock=ClockDomain.HOST,
                            reason="reconnect",
                        )
                    )
                    await self._start_streams(sub.streams)
                    sub._sensor_started, sub._continuous_seen = time.monotonic(), False
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                try:
                    await self._close_connection(cancel_recovery=False)
                except Exception as cleanup_error:
                    error.add_note(f"Reconnect cleanup failed: {cleanup_error}")
                    break
                if not isinstance(error, (DeviceDisconnected, OSError, RequestTimeout)):
                    break
        self._failure = last_error
        if sub is not None:
            sub._fail(last_error or DeviceDisconnected("Reconnect attempts exhausted"))

    async def _close_connection(self, *, cancel_recovery=True):
        current = asyncio.current_task()
        if cancel_recovery and self._recovery is not None and self._recovery is not current:
            self._recovery.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._recovery
            self._recovery = None
        for task in list(self._ceremony_tasks):
            task.cancel()
        for task in list(self._ceremony_tasks):
            with suppress(asyncio.CancelledError, Exception):
                await task
        if self._reader is not None and self._reader is not current:
            self._reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None
        self._fail_replies(DeviceDisconnected("Band connection closed"))
        transport, self._transport = self._transport, None
        try:
            if transport is not None:
                async with asyncio.timeout(self.timeouts.stop + 2):
                    await transport.close()
        finally:
            self._record_base = self.stats.authenticated_records
            self._session = None
            self.hand = None
            self.active_streams = Streams(False)
            self.state = "disconnected"

    async def _close_preserving(self, original_error: BaseException | None):
        try:
            await self._close_connection()
        except BaseException as cleanup_error:
            self.stats.shutdown_errors.append(str(cleanup_error))
            if original_error is None:
                raise
            original_error.add_note(f"Transport cleanup failed: {cleanup_error}")
