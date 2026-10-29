"""Bounded protobuf wire decoder; schemas use checked single-value access."""

from ..errors import ProtocolError

MAX_MESSAGE = 1 << 20


def varint(value: int) -> bytes:
    if not 0 <= value < 1 << 64:
        raise ValueError("Value is outside uint64")
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def field(number: int, value: int | bytes) -> bytes:
    if not 0 < number < 1 << 29:
        raise ValueError("Invalid field number")
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


class Fields:
    def __init__(self, data: bytes):
        if len(data) > MAX_MESSAGE:
            raise ProtocolError("Protobuf message too large")
        self.values: dict[int, list[tuple[int, int | bytes]]] = {}
        offset = 0

        def read_varint() -> int:
            nonlocal offset
            result = 0
            for shift in range(0, 64, 7):
                if offset >= len(data):
                    raise ProtocolError("Truncated protobuf varint")
                byte = data[offset]
                offset += 1
                if shift == 63 and byte > 1:
                    raise ProtocolError("Protobuf varint overflow")
                result |= (byte & 127) << shift
                if byte < 128:
                    return result
            raise ProtocolError("Unterminated protobuf varint")

        while offset < len(data):
            tag = read_varint()
            number, wire = tag >> 3, tag & 7
            if not 0 < number < 1 << 29:
                raise ProtocolError("Invalid protobuf field number")
            value: int | bytes
            if wire == 0:
                value = read_varint()
            else:
                if wire not in (1, 2, 5):
                    raise ProtocolError("Unsupported protobuf wire type")
                size = read_varint() if wire == 2 else {1: 8, 5: 4}[wire]
                if size > len(data) - offset:
                    raise ProtocolError("Truncated protobuf field")
                value = data[offset : offset + size]
                offset += size
            self.values.setdefault(number, []).append((wire, value))

    def __contains__(self, number: int) -> bool:
        return number in self.values

    def _one(self, number: int, wire: int) -> int | bytes:
        values = self.values.get(number, [])
        if len(values) != 1 or values[0][0] != wire:
            raise ProtocolError(f"Expected single protobuf field {number} with wire type {wire}")
        return values[0][1]

    def integer(self, number: int, default: int | None = None) -> int:
        if number not in self and default is not None:
            return default
        value = self._one(number, 0)
        assert isinstance(value, int)
        return value

    def bytes(self, number: int, count: int | None = None) -> bytes:
        value = self._one(number, 2)
        assert isinstance(value, bytes)
        if count is not None and len(value) != count:
            raise ProtocolError(f"Invalid length for protobuf field {number}")
        return value
