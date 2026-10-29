import hashlib
import hmac
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from meta_neural_py.errors import AuthenticationError, ProtocolError
from meta_neural_py.protocol.airshield import AirShieldCipher, AirShieldReceiver, derive_key
from meta_neural_py.protocol.datax import DataXReceiver, frame
from meta_neural_py.protocol.protobuf import Fields, field, varint


def test_varints_all_uint64_boundaries():
    for n in (0, 1, 127, 128, 16384, 2**32, 2**64 - 1):
        assert Fields(field(1, n)).integer(1) == n
    for n in (-1, 2**64):
        with pytest.raises(ValueError):
            varint(n)


@pytest.mark.parametrize(
    "data", [b"\0", b"\x08\x80", b"\x08" + b"\xff" * 10, b"\x0a\x03x", b"\x0b", b"\x0d123"]
)
def test_checked_protobuf_rejects_invalid_fields(data):
    with pytest.raises(ProtocolError):
        Fields(data)


def test_duplicate_or_wrong_type_not_defaulted():
    f = Fields(field(1, 1) + field(1, 2) + field(2, b"x"))
    for n in (1, 2):
        with pytest.raises(ProtocolError):
            f.integer(n, 0)
    assert f.integer(3, 9) == 9


def test_key_derivation_matches_independent_hkdf():
    secret, challenge, seed = bytes(range(32)), bytes(range(16)), bytes(range(32, 64))
    hashed = hashlib.sha256(secret).digest()
    salt = hashlib.sha256(hashed + challenge + seed).digest()
    prk = hmac.digest(salt, hashed, "sha256")
    expected = hmac.digest(prk, b"AirShield\x01", "sha256")
    assert derive_key(secret, challenge, seed) == expected


@pytest.mark.parametrize("size", [4, 16, 31, 256, 4096])
def test_records_match_independent_aes_hmac_and_arbitrary_fragmentation(size):
    key, iv, counter = bytes(range(32)), bytes(range(16)), 0xFFFFFFFF
    plain = b"x" * size
    padded = plain + bytes([0xC0 + (-size) % 16]) * ((-size) % 16)
    independent = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(padded)
    body = bytes([len(padded) // 16 - 1]) + independent
    expected = b"\x40" + hmac.digest(key, struct.pack("<I", counter) + body, "sha256")[:8] + body
    sender = AirShieldCipher(key, iv, counter)
    packet = sender.encrypt(plain)
    assert packet == expected
    receiver = AirShieldReceiver(AirShieldCipher(key, iv, counter))
    results = []
    for byte in packet + sender.encrypt(plain):
        results.extend(receiver.feed(bytes([byte])))
    assert results == [padded, padded]
    receiver.finish()


def test_mac_failure_does_not_advance_cipher_or_expose_plaintext():
    cipher = AirShieldCipher(bytes(32), bytes(16), 3)
    packet = AirShieldCipher(bytes(32), bytes(16), 3).encrypt(b"hello")
    damaged = bytearray(packet)
    damaged[-1] ^= 1
    with pytest.raises(AuthenticationError):
        cipher.decrypt(bytes(damaged))
    assert cipher.decrypt(packet).startswith(b"hello")


def test_record_controls_coalescing_truncation_and_unknown_marker():
    sender = AirShieldCipher(bytes(32), bytes(16), 2)
    receiver = AirShieldReceiver(AirShieldCipher(bytes(32), bytes(16), 2))
    data = b"\x81\0\x01\x01xx" + sender.encrypt(bytes(16))
    assert receiver.feed(data) == [bytes(16)]
    receiver.feed(b"\x40")
    with pytest.raises(ProtocolError):
        receiver.finish()
    with pytest.raises(ProtocolError):
        AirShieldReceiver(AirShieldCipher(bytes(32), bytes(16), 0)).feed(b"\xff")


def test_datax_fragmentation_continuations_and_padding():
    original = frame(0x8001, (0x81000005, 0x02000001), field(1, bytes(40)))
    second = frame(5, (), field(1, 3))
    tx, rx = (
        AirShieldCipher(bytes(32), bytes(16), 0),
        AirShieldReceiver(AirShieldCipher(bytes(32), bytes(16), 0)),
    )
    datax = DataXReceiver()
    records = tx.encrypt(original[:16]) + tx.encrypt(original[16:] + second)
    frames = [f for p in rx.feed(records) for f in datax.feed(p)]
    assert frames[0].payload == field(1, bytes(40))
    assert frames[1].words == ()
    datax.finish()


def test_invalid_datax_header_and_incomplete_frame():
    with pytest.raises(ProtocolError):
        DataXReceiver().feed(b"\x80\x01\0\x01x", padded=False)
    parser = DataXReceiver()
    parser.feed(b"\0\x10\0\x01x", padded=False)
    with pytest.raises(ProtocolError):
        parser.finish()


@pytest.mark.parametrize("padding", range(1, 16))
def test_aligned_payload_suffix_is_not_mistaken_for_padding(padding):
    # These are 32-byte complete frames, with legitimate sensor bytes equal to
    # the transport padding pattern. Declared frame lengths disambiguate them.
    payload = bytes(28 - padding) + bytes([0xC0 + padding]) * padding
    wire = frame(2, (), payload)
    assert len(wire) == 32
    assert DataXReceiver().feed(wire)[0].payload == payload
