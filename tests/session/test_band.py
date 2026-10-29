import asyncio
import time
from dataclasses import replace

import pytest
from conftest import DEVICE_ID, Peer

from meta_neural_py import (
    Band,
    Discontinuity,
    EMGBatch,
    Gesture,
    ReconnectPolicy,
    Streams,
    Timeouts,
)
from meta_neural_py.errors import (
    DeviceBusy,
    DeviceDisconnected,
    IdentityMismatch,
    QueueOverflow,
    RequestTimeout,
    ServiceUnavailable,
    StreamShutdownError,
    TrustVerificationFailed,
)
from meta_neural_py.identity.store import MemoryIdentityStore
from meta_neural_py.protocol.protobuf import Fields


def band_for(peer, store, tmp_path, **kwargs):
    return Band(
        DEVICE_ID,
        identity_store=store,
        transport_factory=lambda: peer,
        lock_directory=tmp_path,
        **kwargs,
    )


@pytest.mark.parametrize("proof_first", [False, True])
async def test_authenticated_public_api_and_acknowledged_stop(peer, store, tmp_path, proof_first):
    peer.proof_first = proof_first
    async with band_for(peer, store, tmp_path) as band:
        assert band.state == "connected" and band.device_info.fields[1] == b"synthetic-firmware"
        assert not band.active_streams.any
        assert await band.set_hand("left") == "left"
        assert (await band.read_battery()).level == 75
        async with band.subscribe(Streams(gyro=True)) as events:
            peer.sensor()
            gesture = await asyncio.wait_for(anext(events), 1)
            assert isinstance(gesture, Gesture) and gesture.finger == "index"
            assert band.active_streams.gyro
        assert not band.active_streams.any
        assert peer.flags == {2: 0, 3: 0, 6: 0, 8: 0}
    assert peer.closed


async def test_service_rejection_immediate_not_hand_timeout(peer, store, tmp_path):
    peer.reject = 6
    async with band_for(peer, store, tmp_path) as band:
        started = time.monotonic()
        with pytest.raises(ServiceUnavailable) as caught:
            await band.read_hand()
        assert time.monotonic() - started < 1
        assert caught.value.code == 0x0300C001
        assert caught.value.operation == "read_hand" and caught.value.channel == 6


async def test_device_info_rejection_prevents_input_requests(peer, store, tmp_path):
    peer.reject = 3
    with pytest.raises(ServiceUnavailable):
        async with band_for(peer, store, tmp_path):
            pytest.fail("Rejected device info must not connect")
    assert peer.closed
    assert not any(f.channel == 0x8005 for f in peer.requests)


async def test_invalid_proof_is_not_acknowledged(peer, store, tmp_path):
    peer.bad_proof = True
    with pytest.raises(TrustVerificationFailed):
        async with band_for(peer, store, tmp_path):
            pass
    assert not any(p.channel == 3 and p.words == (0x03001000,) for p in peer.requests)
    assert not any(p.channel == 0x8001 for p in peer.requests)
    assert store.load(DEVICE_ID) is not None


async def test_no_identity_does_not_open_transport(tmp_path):
    peer = Peer()
    with pytest.raises(IdentityMismatch):
        async with band_for(peer, MemoryIdentityStore(), tmp_path):
            pass
    assert not peer.requests


async def test_only_one_subscription_and_emg_configuration(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        async with band.subscribe(Streams(gestures=False, emg=True)) as events:
            with pytest.raises(DeviceBusy):
                async with band.subscribe(Streams()):
                    pass
            peer.sensor(0x0200020A)
            event = await asyncio.wait_for(anext(events), 1)
            assert isinstance(event, EMGBatch) and event.values[-1][-1] == 127
            assert band.emg_configuration is not None and band.emg_configuration.supported
            assert band.stats.emg_samples == 16
            config = [i for i, p in enumerate(peer.requests) if p.channel == 0x8007]
            enabled = [
                i
                for i, p in enumerate(peer.requests)
                if p.channel == 0x8005
                and 4 in Fields(p.payload)
                and Fields(Fields(p.payload).bytes(4)).integer(2, 0) == 1
            ]
            assert config[0] < enabled[0]


@pytest.mark.parametrize("policy,expected", [("drop_oldest", 3), ("drop_newest", 1)])
async def test_bounded_queue_drop_policies(peer, store, tmp_path, policy, expected):
    async with band_for(peer, store, tmp_path) as band:
        async with band.subscribe(Streams(), queue_size=1, overflow=policy) as events:
            for n in (1, 2, 3):
                peer.sensor(sequence=n, timestamp=n)
            await asyncio.sleep(0.05)
            assert (await anext(events)).sequence == expected
            assert band.stats.queue_drops == 2


async def test_default_overflow_fails_visibly(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        async with band.subscribe(Streams(), queue_size=1) as events:
            peer.sensor(sequence=1)
            peer.sensor(sequence=2)
            await asyncio.sleep(0.05)
            with pytest.raises(QueueOverflow):
                await anext(events)


async def test_sequence_gap_and_unknown_wrap_separate_from_queue_loss(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        async with band.subscribe(Streams()) as events:
            for seq, timestamp in [(10, 10), (13, 13), (0, 1)]:
                peer.sensor(sequence=seq, timestamp=timestamp)
            result = [await asyncio.wait_for(anext(events), 1) for _ in range(5)]
            assert isinstance(result[1], Discontinuity) and result[1].missing is None
            assert result[3].missing is None
            assert band.stats.sequence_gaps is None and band.stats.queue_drops == 0


async def test_rpc_timeout_and_optional_battery_failure(peer, store, tmp_path):
    timeouts = replace(Timeouts(), rpc=0.08)
    async with band_for(peer, store, tmp_path, timeouts=timeouts) as band:
        peer.silence = 6
        with pytest.raises(RequestTimeout):
            await band.read_hand()
        peer.reject = 8
        assert await band.read_battery() is None
        async with band.subscribe(Streams()) as events:
            peer.sensor()
            assert isinstance(await asyncio.wait_for(anext(events), 1), Gesture)


async def test_cancellation_stops_and_releases_lock(peer, store, tmp_path):
    async def consume():
        async with band_for(peer, store, tmp_path) as band:
            async with band.subscribe(Streams()) as events:
                await anext(events)

    task = asyncio.create_task(consume())
    while not peer.flags[3]:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not peer.flags[3] and peer.closed
    from meta_neural_py.transport.lock import DeviceLock

    lock = DeviceLock(DEVICE_ID, tmp_path)
    lock.acquire()
    lock.release()


async def test_shutdown_failure_does_not_hide_original_error(peer, store, tmp_path):
    band = band_for(peer, store, tmp_path, timeouts=replace(Timeouts(), stop=0.08))
    with pytest.raises(ValueError, match="consumer failed") as caught:
        async with band:
            async with band.subscribe(Streams()):
                peer.silence = 5
                raise ValueError("consumer failed")
    assert caught.value.__notes__ and band.stats.shutdown_errors
    assert peer.closed


async def test_unacknowledged_stop_closes_connection(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path, timeouts=replace(Timeouts(), stop=0.08)) as band:
        with pytest.raises(StreamShutdownError):
            async with band.subscribe(Streams()):
                peer.silence = 5
        assert peer.closed


async def test_reconnect_repeats_trust_and_emits_discontinuity(peer, store, tmp_path):
    next_peer = Peer(identity=peer.identity, band_key=peer.band_key)
    peers = iter([peer, next_peer])
    band = Band(
        DEVICE_ID,
        identity_store=store,
        transport_factory=lambda: next(peers),
        lock_directory=tmp_path,
        reconnect=ReconnectPolicy(attempts=1, delay=0),
    )
    async with band:
        async with band.subscribe(Streams()) as events:
            peer._out.put_nowait(DeviceDisconnected("synthetic disconnect"))
            event = await asyncio.wait_for(anext(events), 2)
            assert isinstance(event, Discontinuity) and event.reason == "reconnect"
            while not next_peer.flags[3]:
                await asyncio.sleep(0.01)
            next_peer.sensor()
            assert isinstance(await asyncio.wait_for(anext(events), 1), Gesture)
            assert band.stats.reconnects == 1


async def test_concurrent_hand_changes_each_get_independent_readback(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        assert await asyncio.gather(band.set_hand("left"), band.set_hand("right")) == [
            "left",
            "right",
        ]


async def test_cleanup_failure_preserves_consumer_error_and_releases_lock(peer, store, tmp_path):
    async def fail_close():
        peer.closed = True
        raise OSError("synthetic close failure")

    peer.close = fail_close
    with pytest.raises(ValueError, match="original failure") as caught:
        async with band_for(peer, store, tmp_path):
            raise ValueError("original failure")
    assert any("synthetic close failure" in note for note in caught.value.__notes__)
    from meta_neural_py.transport.lock import DeviceLock

    lock = DeviceLock(DEVICE_ID, tmp_path)
    lock.acquire()
    lock.release()


async def test_first_sensor_timeout_is_distinct_from_ack_traffic(peer, store, tmp_path):
    timeouts = replace(Timeouts(), first_sensor=0.08)
    async with band_for(peer, store, tmp_path, timeouts=timeouts) as band:
        async with band.subscribe(Streams(gyro=True)) as events:
            assert (await band.read_battery()).level == 75
            with pytest.raises(RequestTimeout) as caught:
                await asyncio.wait_for(anext(events), 1)
            assert caught.value.operation == "first_sensor"
            assert band.stats.last_traffic_at is not None and band.stats.last_sensor_at is None


async def test_rpc_response_is_correlated_by_channel_and_request_id(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        peer.silence = 6
        task = asyncio.create_task(band.read_hand())
        while not any(p.channel == 0x8006 for p in peer.requests):
            await asyncio.sleep(0.001)
        pending = [p for p in peer.requests if p.channel == 0x8006][-1]
        request_id = Fields(pending.payload).integer(1)
        from meta_neural_py.protocol.protobuf import field

        body = field(1, request_id) + field(2, 1) + field(6, field(10, 1))
        peer.send(7, (0x02000315,), body)
        peer.send(
            6, (0x02000315,), field(1, request_id + 50) + field(2, 1) + field(6, field(10, 1))
        )
        await asyncio.sleep(0.02)
        assert not task.done()
        peer.send(6, (0x02000315,), body)
        assert await asyncio.wait_for(task, 1) == "left"


async def test_sequence_continuity_resets_at_subscription_boundaries(peer, store, tmp_path):
    async with band_for(peer, store, tmp_path) as band:
        for seq in (10, 100):
            async with band.subscribe(Streams()) as events:
                peer.sensor(sequence=seq)
                assert isinstance(await asyncio.wait_for(anext(events), 1), Gesture)
        assert band.stats.sequence_gaps is None


async def test_missing_band_key_requires_explicit_policy_and_reports_unverified(peer, tmp_path):
    from meta_neural_py.identity.store import Identity

    store = MemoryIdentityStore()
    store.save(DEVICE_ID, Identity(peer.identity.private_key, None, allow_missing_band_key=True))
    async with band_for(peer, store, tmp_path) as band:
        assert not band.band_verified
        assert await band.read_hand() == "right"


async def test_compatibility_mode_does_not_skip_verification_of_available_key(
    peer, store, tmp_path
):
    peer.bad_proof = True
    store.save(DEVICE_ID, replace(peer.identity, allow_missing_band_key=True))
    with pytest.raises(TrustVerificationFailed):
        async with band_for(peer, store, tmp_path):
            pass
