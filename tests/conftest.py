"""All identities, nonces, and sensor bytes here are disposable synthetic data."""

import asyncio
import struct

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from meta_neural_py.identity.store import Identity, MemoryIdentityStore
from meta_neural_py.identity.trust import digest, load_point, public_point, sign, verify
from meta_neural_py.protocol.airshield import AirShieldCipher, AirShieldReceiver, derive_key
from meta_neural_py.protocol.datax import DataXReceiver, frame
from meta_neural_py.protocol.protobuf import Fields, field

DEVICE_ID = "00000000-0000-0000-0000-000000000001"


class Peer:
    """An independent scripted band endpoint, including real ECDH and signatures."""

    def __init__(
        self,
        identity=None,
        band_key=None,
        *,
        proof_first=False,
        bad_proof=False,
        reject=None,
        silence=None,
        accepted_flags=None,
        enrollment=False,
    ):
        self.app_key = identity.private_key if identity else ec.generate_private_key(ec.SECP256R1())
        self.app_public_key = self.app_key.public_key()
        self.band_key = band_key or ec.generate_private_key(ec.SECP256R1())
        self.identity = Identity(self.app_key, self.band_key.public_key())
        self.transport_key = ec.derive_private_key(1, ec.SECP256R1())
        self.point = public_point(self.transport_key.public_key())
        self.challenge, self.seed, self.iv = (
            bytes(range(16)),
            bytes(range(32)),
            bytes(range(16, 32)),
        )
        self.proof_first, self.bad_proof = proof_first, bad_proof
        self.reject, self.silence, self.accepted_flags = reject, silence, accepted_flags
        self.enrollment = enrollment
        self.sender = self.receiver = None
        self.clear = DataXReceiver()
        self.datax = DataXReceiver()
        self.host_point = self.host_challenge = None
        self.requests = []
        self._out = asyncio.Queue()
        self.closed = False
        self.hand = 0
        self.flags = {2: 0, 3: 0, 6: 0, 8: 0}
        self.channel_types = {}

    async def connect(self, device_id, timeout):
        assert device_id == DEVICE_ID

    async def read(self):
        value = await self._out.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self):
        self.closed = True

    def wire(self, channel, words=(), payload=b""):
        return self.sender.encrypt(frame(channel, words, payload))

    def send(self, channel, words=(), payload=b""):
        self._out.put_nowait(self.wire(channel, words, payload))

    def proof(self):
        key = ec.generate_private_key(ec.SECP256R1()) if self.bad_proof else self.band_key
        signature = sign(key, digest(self.host_challenge, self.host_point, self.seed, self.point))
        return frame(
            0x8003,
            (0x81000024, 0x02001001),
            field(1, b"hint" * 3) + field(2, signature) + field(3, 1),
        )

    async def write(self, data):
        if self.receiver is None:
            for packet in self.clear.feed(data, padded=False):
                f = Fields(packet.payload)
                if packet.words[-1] == 0x02000001:
                    self.host_point, self.host_challenge = f.bytes(1, 64), f.bytes(2, 16)
                    self._out.put_nowait(
                        frame(
                            0x8001,
                            (0x81000005, 0x02000001),
                            field(1, self.point)
                            + field(2, self.challenge)
                            + field(3, 0)
                            + field(4, 3),
                        )
                    )
                else:
                    secret = self.transport_key.exchange(ec.ECDH(), load_point(self.host_point))
                    self.host_seed = f.bytes(2, 32)
                    self.receiver = AirShieldReceiver(
                        AirShieldCipher(
                            derive_key(secret, self.challenge, self.host_seed),
                            f.bytes(3, 16),
                            f.integer(4),
                        )
                    )
                    self.sender = AirShieldCipher(
                        derive_key(secret, self.host_challenge, self.seed), self.iv, 42
                    )
                    self._out.put_nowait(
                        frame(
                            1,
                            (0x02000002,),
                            field(1, self.point)
                            + field(2, self.seed)
                            + field(3, self.iv)
                            + field(4, 42)
                            + field(5, 3),
                        )
                    )
            return
        for plain in self.receiver.feed(data):
            for packet in self.datax.feed(plain):
                self.requests.append(packet)
                kind = packet.words[-1] if packet.words else self.channel_types.get(packet.channel)
                if packet.words:
                    self.channel_types[packet.channel] = kind
                f = Fields(packet.payload)
                if packet.channel == 0x8002:
                    if kind == 0x02003000:
                        self.send(
                            2,
                            (0x02003001,),
                            field(1, b"c" * 300) + field(2, b"serial") + field(5, b"s" * 300),
                        )
                    elif kind == 0x02002000:
                        self.send(2, (0x02002001,), field(1, bytes(16)))
                    elif kind == 0x02002002:
                        assert f.bytes(2).decode() == ' {"pending": 1} '
                        self.send(
                            2,
                            (0x02002003,),
                            field(1, b"device-signature") + field(2, b' {"device": 2} '),
                        )
                    elif kind == 0x02002004:
                        assert f.bytes(2).decode() == '\n{"final": 3}\n'
                        self.send(2, (0x02002005,))
                    elif kind == 0x02001000:
                        # Verify the host proof independently from Session.
                        verify(
                            self.app_public_key,
                            f.bytes(2, 64),
                            digest(self.challenge, self.point, self.host_seed, self.host_point),
                        )
                        result = frame(2, (0x03001000,))
                        proof = self.proof()
                        combined = proof + result if self.proof_first else result + proof
                        # Fragment clear DataX over authenticated records and IPC reads.
                        self._out.put_nowait(self.sender.encrypt(combined[:16]))
                        encrypted = self.sender.encrypt(combined[16:])
                        self._out.put_nowait(encrypted[:7])
                        self._out.put_nowait(encrypted[7:])
                elif packet.channel == 0x8001:
                    self.send(0x8001, (0x02001000,), field(1, 1) + field(2, b"e" * 16))
                elif kind == 0x02000314:
                    channel, request_id = packet.channel & 0x7FFF, f.integer(1)
                    if channel == self.silence:
                        continue
                    if channel == self.reject:
                        self.send(channel, (0x0300C001,))
                        continue
                    body = field(1, request_id) + field(2, 1)
                    if channel == 3:
                        body += field(4, field(1, b"synthetic-firmware"))
                    elif channel == 5:
                        changes = Fields(f.bytes(4))
                        for n in (2, 3, 6, 8):
                            if n in changes:
                                self.flags[n] = changes.integer(n)
                        body += field(
                            5,
                            b"".join(
                                field(n, v) for n, v in (self.accepted_flags or self.flags).items()
                            ),
                        )
                    elif channel == 6:
                        changes = Fields(f.bytes(5))
                        if 10 in changes:
                            assert list(changes.values) == [10]
                            self.hand = changes.integer(10)
                        body += field(6, field(10, self.hand))
                    elif channel == 7:
                        config = b"".join(
                            field(n, v) for n, v in [(1, 2048), (2, 8), (4, 16), (5, 16), (10, 0)]
                        )
                        body += field(6, field(42, config))
                    elif channel == 8:
                        body += field(3, field(1, field(1, 75) + field(2, 1)))
                    self.send(channel, (0x02000315,), body)

    def sensor(self, kind=0x0200020D, sequence=1, timestamp=100):
        body = field(1, sequence) + field(2, timestamp)
        if kind == 0x0200020D:
            body += field(3, 2) + field(4, 3) + field(5, 1)
        elif kind == 0x0200020F:
            body += field(3, struct.pack("<hhh", -32768, 0, 32767))
        elif kind == 0x02000212:
            body += field(3, struct.pack("<ffff", 1, 0, 0, 0))
        else:
            body += field(3, struct.pack("<128H", *range(128)))
        self.send(0x800A, (kind,), body)


@pytest.fixture
def peer():
    return Peer()


@pytest.fixture
def store(peer):
    store = MemoryIdentityStore()
    store.save(DEVICE_ID, peer.identity)
    return store
