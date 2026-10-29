import pytest
from conftest import DEVICE_ID

from meta_neural_py import Band
from meta_neural_py.errors import RequestTimeout, TrustVerificationFailed
from meta_neural_py.models import Streams
from meta_neural_py.protocol.protobuf import field
from meta_neural_py.protocol.session import Session


def test_startup_deadline_boundary(peer):
    session = Session(peer.identity)
    session.start(10)
    assert session.tick(19.999) == []
    with pytest.raises(RequestTimeout) as caught:
        session.tick(20)
    assert caught.value.stage == "encryption"


async def test_duplicate_band_proof_fails_after_ready(peer, store, tmp_path):
    async with Band(
        DEVICE_ID, identity_store=store, transport_factory=lambda: peer, lock_directory=tmp_path
    ) as band:
        peer._out.put_nowait(peer.sender.encrypt(peer.proof()))
        import asyncio

        await asyncio.sleep(0.05)
        with pytest.raises(TrustVerificationFailed):
            await band.read_hand()


async def test_untyped_sensor_continuation_keeps_type(peer, store, tmp_path):
    async with Band(
        DEVICE_ID, identity_store=store, transport_factory=lambda: peer, lock_directory=tmp_path
    ) as band:
        async with band.subscribe(Streams()) as events:
            peer.sensor()
            peer.send(0x800A, (), field(1, 2) + field(2, 101) + field(3, 3) + field(4, 1))
            first, second = await anext(events), await anext(events)
            assert first.action == "tap" and second.finger == "middle" and second.action == "press"
