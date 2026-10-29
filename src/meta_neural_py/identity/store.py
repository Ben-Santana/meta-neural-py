from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric import ec

from ..errors import IdentityStoreError
from .trust import load_point, public_point


@dataclass(frozen=True)
class Identity:
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)
    band_public_key: ec.EllipticCurvePublicKey | None = field(repr=False)
    allow_missing_band_key: bool = False

    def __post_init__(self):
        if type(self.allow_missing_band_key) is not bool:
            raise IdentityStoreError("Invalid band verification policy")
        if self.band_public_key is None and not self.allow_missing_band_key:
            raise IdentityStoreError(
                "Band public key is required unless compatibility mode is explicit"
            )
        if not isinstance(self.private_key.curve, ec.SECP256R1) or (
            self.band_public_key is not None
            and not isinstance(self.band_public_key.curve, ec.SECP256R1)
        ):
            raise IdentityStoreError("Identity keys must use P-256")


@dataclass(frozen=True)
class PendingEnrollment:
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)
    stage: str
    band_point: bytes | None = field(default=None, repr=False)
    allow_missing_band_key: bool = False


class IdentityStore(Protocol):
    def load(self, device_id: str) -> Identity | None: ...
    def save(self, device_id: str, identity: Identity) -> None: ...
    def load_pending(self, device_id: str) -> PendingEnrollment | None: ...
    def save_pending(self, device_id: str, pending: PendingEnrollment) -> None: ...
    def clear_pending(self, device_id: str) -> None: ...


class MemoryIdentityStore:
    """For tests or explicitly ephemeral sessions; never used by default."""

    def __init__(self):
        self._identities: dict[str, Identity] = {}
        self._pending: dict[str, PendingEnrollment] = {}

    def load(self, device_id):
        return self._identities.get(device_id)

    def save(self, device_id, identity):
        self._identities[device_id] = identity

    def load_pending(self, device_id):
        return self._pending.get(device_id)

    def save_pending(self, device_id, pending):
        self._pending[device_id] = pending

    def clear_pending(self, device_id):
        self._pending.pop(device_id, None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Invalid encoded field")
    return base64.b64decode(value, validate=True)


def encode_record(
    key: ec.EllipticCurvePrivateKey,
    point: bytes | None,
    stage: str,
    *,
    allow_missing_band_key: bool = False,
) -> bytes:
    return json.dumps(
        {
            "version": 1,
            "private_key": _b64(key.private_numbers().private_value.to_bytes(32, "big")),
            "band_key": _b64(point) if point is not None else None,
            "stage": stage,
            "allow_missing_band_key": allow_missing_band_key,
        },
        separators=(",", ":"),
    ).encode()


def decode_record(data: bytes) -> PendingEnrollment:
    try:
        if len(data) > 8192:
            raise ValueError()
        record = json.loads(data)
        if record["version"] != 1 or record["stage"] not in (
            "identity",
            "claim",
            "finish",
            "confirmed",
            "verified",
            "host_verified",
        ):
            raise ValueError()
        raw = _unb64(record["private_key"])
        if len(raw) != 32:
            raise ValueError()
        key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
        point = _unb64(record["band_key"]) if record.get("band_key") is not None else None
        if point is not None:
            load_point(point)
        allow_missing = record.get("allow_missing_band_key", False)
        if type(allow_missing) is not bool:
            raise ValueError()
        return PendingEnrollment(key, record["stage"], point, allow_missing)
    except Exception:
        raise IdentityStoreError("Invalid saved identity record") from None


def import_kinesis_record(data: bytes) -> Identity:
    """Explicit file import; does not read another application's Keychain."""
    try:
        if len(data) > 8192:
            raise ValueError()
        record = json.loads(data)
        raw = _unb64(record["AppPrivateKey"])
        if len(raw) != 32:
            raise ValueError()
        key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
        if _unb64(record["AppPublicKey"]) != public_point(key.public_key()):
            raise ValueError()
        a, b = record.get("AppECPubicKey"), record.get("AppECPublicKey")
        if a is not None and b is not None and a != b:
            raise ValueError()
        return Identity(key, load_point(_unb64(a if a is not None else b)))
    except Exception:
        raise IdentityStoreError(
            "Invalid identity import: matching host keys and a valid band public key are required"
        ) from None
