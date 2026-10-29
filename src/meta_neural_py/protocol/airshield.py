"""Parameter-3 AirShield; authenticate before releasing any plaintext."""

import hashlib
import hmac
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..errors import AuthenticationError, ProtocolError


def derive_key(secret: bytes, challenge: bytes, seed: bytes) -> bytes:
    if (len(secret), len(challenge), len(seed)) != (32, 16, 32):
        raise ProtocolError("Invalid AirShield key material")
    hashed = hashlib.sha256(secret).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(hashed + challenge + seed).digest(),
        info=b"AirShield",
    ).derive(hashed)


class AirShieldCipher:
    def __init__(self, key: bytes, iv: bytes, counter: int):
        if len(key) != 32 or len(iv) != 16 or not 0 <= counter <= 0xFFFFFFFF:
            raise ProtocolError("Invalid AirShield cipher parameters")
        self._key, self._iv, self._counter = key, iv, counter
        self._records = 0

    def _advance(self, ciphertext: bytes):
        self._iv = ciphertext[-16:]
        self._counter = (self._counter + 1) & 0xFFFFFFFF
        self._records += 1

    def _tag(self, body: bytes) -> bytes:
        if self._records >= 1 << 32:
            raise AuthenticationError("AirShield counter exhausted; reconnect")
        return hmac.digest(self._key, struct.pack("<I", self._counter) + body, "sha256")[:8]

    def encrypt(self, data: bytes) -> bytes:
        padding = (-len(data)) % 16
        data += bytes([0xC0 + padding]) * padding
        if not data or len(data) > 4096:
            raise ProtocolError("Invalid AirShield plaintext size")
        encryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()
        body = bytes([len(ciphertext) // 16 - 1]) + ciphertext
        packet = b"\x40" + self._tag(body) + body
        self._advance(ciphertext)
        return packet

    def decrypt(self, packet: bytes) -> bytes:
        if len(packet) < 26 or packet[0] != 0x40 or len(packet) != 10 + (packet[9] + 1) * 16:
            raise ProtocolError("Invalid AirShield packet shape")
        if not hmac.compare_digest(packet[1:9], self._tag(packet[9:])):
            raise AuthenticationError("AirShield packet authentication failed")
        ciphertext = packet[10:]
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
        result = decryptor.update(ciphertext) + decryptor.finalize()
        self._advance(ciphertext)
        return result


class AirShieldReceiver:
    def __init__(self, cipher: AirShieldCipher):
        self.cipher = cipher
        self._pending = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._pending.extend(data)
        result = []
        while self._pending:
            marker = self._pending[0]
            if marker in (0x81, 0x82):
                if len(self._pending) < 2:
                    break
                if self._pending[1] > 1:
                    raise ProtocolError("Unsupported AirShield control marker")
                del self._pending[:2]
                continue
            if marker in (1, 2):
                if len(self._pending) < 2 or len(self._pending) < 3 + self._pending[1]:
                    break
                del self._pending[: 3 + self._pending[1]]
                continue
            if marker not in (0x40, 0x41, 0x42):
                raise ProtocolError("Unsupported AirShield transport marker")
            if len(self._pending) < 10:
                break
            size = 10 + (self._pending[9] + 1) * 16
            if len(self._pending) < size:
                break
            if marker == 0x40:
                result.append(self.cipher.decrypt(bytes(self._pending[:size])))
            del self._pending[:size]
        return result

    def finish(self):
        if self._pending:
            raise ProtocolError("Truncated AirShield packet")
