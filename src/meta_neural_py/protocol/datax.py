import struct
from dataclasses import dataclass

from ..errors import ProtocolError


@dataclass(frozen=True)
class Frame:
    channel: int
    words: tuple[int, ...]
    payload: bytes = b""


def frame(channel: int, words=(), payload: bytes = b"") -> bytes:
    if not 0 <= channel <= 0xFFFF:
        raise ValueError("Channel outside uint16")
    words = tuple(words)
    if any(bool(w & 0x80000000) != (i < len(words) - 1) for i, w in enumerate(words)):
        raise ValueError("Invalid DataX type continuation bits")
    body = b"".join(struct.pack(">I", w) for w in words) + payload
    if len(body) > 0x7FFF:
        raise ProtocolError("DataX frame too large")
    return struct.pack(">HH", len(body) | (0x8000 if words else 0), channel) + body


class DataXReceiver:
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data: bytes, *, padded: bool = True) -> list[Frame]:
        if padded:
            if not data or len(data) % 16:
                raise ProtocolError("Unaligned DataX plaintext")
            padding = data[-1] - 0xC0
            if (
                1 <= padding <= 15
                and data[-padding:] == bytes([0xC0 + padding]) * padding
                and not self._ends_at_boundary(data)
            ):
                data = data[:-padding]
        self.pending.extend(data)
        result = []
        while len(self.pending) >= 4:
            length, channel = struct.unpack_from(">HH", self.pending)
            size = (length & 0x7FFF) + 4
            if len(self.pending) < size:
                break
            offset, words = 4, []
            if length & 0x8000:
                while True:
                    if offset + 4 > size:
                        raise ProtocolError("Truncated DataX typed header")
                    word = struct.unpack_from(">I", self.pending, offset)[0]
                    words.append(word)
                    offset += 4
                    if not word & 0x80000000:
                        break
            result.append(Frame(channel, tuple(words), bytes(self.pending[offset:size])))
            del self.pending[:size]
        return result

    def _ends_at_boundary(self, data: bytes) -> bool:
        # An aligned, unpadded payload can itself end in 0xc1..0xcf.
        # Keep that suffix when the declared frame lengths consume it exactly.
        combined = self.pending + data
        offset = 0
        while offset + 4 <= len(combined):
            size = (int.from_bytes(combined[offset : offset + 2], "big") & 0x7FFF) + 4
            offset += size
            if offset == len(combined):
                return True
        return False

    def finish(self):
        if self.pending:
            raise ProtocolError("Truncated DataX frame")
