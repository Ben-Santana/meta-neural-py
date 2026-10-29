"""Pure BLE ownership state machine. Receipts remain exact UTF-8 strings."""

from dataclasses import dataclass
from dataclasses import field as dc_field

from cryptography.hazmat.primitives.asymmetric import ec

from ..errors import EnrollmentError
from ..protocol.datax import frame
from ..protocol.protobuf import Fields, field
from .store import Identity
from .trust import load_point, public_point


@dataclass(frozen=True)
class PairRequest:
    certificate: bytes = dc_field(repr=False)
    serial: str
    secondary_certificate: bytes = dc_field(repr=False)
    nonce: bytes = dc_field(repr=False)
    app_public_key: bytes = dc_field(repr=False)


@dataclass(frozen=True)
class Pair:
    receipt: str = dc_field(repr=False)
    signature: bytes = dc_field(repr=False)


@dataclass(frozen=True)
class PairCompletion:
    signature: bytes = dc_field(repr=False)
    receipt: str = dc_field(repr=False)
    band_public_key: bytes | None = dc_field(default=None, repr=False)

    def __post_init__(self):
        if not self.signature or not self.receipt:
            raise EnrollmentError("Ownership service returned an empty receipt or signature")


class OwnershipCeremony:
    def __init__(
        self, band_public_key: bytes | None = None, *, allow_missing_band_key: bool = False
    ):
        if type(allow_missing_band_key) is not bool:
            raise EnrollmentError("Invalid band verification policy")
        self.allow_missing_band_key = allow_missing_band_key
        if band_public_key is not None:
            load_point(band_public_key)
        self.expected_band_point = band_public_key
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.stage = "identity"
        self._identity: tuple[bytes, str, bytes] | None = None
        self._band_key: ec.EllipticCurvePublicKey | None = None

    def start(self) -> bytes:
        if self.stage != "identity":
            raise EnrollmentError("Enrollment already started")
        return frame(0x8002, (0x81000024, 0x02003000))

    def receive(self, kind: int, payload: bytes) -> bytes | PairRequest | Pair | Identity:
        if self.stage == "identity" and kind == 0x02003001:
            f = Fields(payload)
            certificate, serial, secondary = f.bytes(1), f.bytes(2), f.bytes(5)
            try:
                serial_text = serial.decode("ascii")
            except UnicodeError:
                raise EnrollmentError("Invalid band serial") from None
            if len(certificate) <= 256 or len(secondary) <= 256 or not serial_text:
                raise EnrollmentError("Band identity is incomplete")
            self._identity = certificate, serial_text, secondary
            self.stage = "nonce"
            return frame(0x8002, (0x02002000,))
        if self.stage == "nonce" and kind == 0x02002001:
            nonce = Fields(payload).bytes(1, 16)
            assert self._identity is not None
            self.stage = "pair_request"
            return PairRequest(*self._identity, nonce, public_point(self.private_key.public_key()))
        if self.stage == "start" and kind == 0x02002003:
            f = Fields(payload)
            signature = f.bytes(1)
            try:
                receipt = f.bytes(2).decode("utf-8")
            except UnicodeError:
                raise EnrollmentError("Invalid device receipt encoding") from None
            if not signature or not receipt:
                raise EnrollmentError("Band returned an empty ownership receipt")
            self.stage = "pair"
            return Pair(receipt, signature)
        if self.stage == "finish" and kind == 0x02002005 and not payload:
            self.stage = "confirmed"
            return Identity(self.private_key, self._band_key, self.allow_missing_band_key)
        raise EnrollmentError("Unexpected ownership ceremony response", stage=self.stage)

    def resume(self, completion: PairCompletion) -> bytes:
        if self.stage == "pair_request":
            self.stage, kind = "start", 0x02002002
        elif self.stage == "pair":
            point = completion.band_public_key or self.expected_band_point
            if point is None and not self.allow_missing_band_key:
                raise EnrollmentError(
                    "Ownership service omitted band public key; verified enrollment cannot finish"
                )
            if (
                completion.band_public_key is not None
                and self.expected_band_point is not None
                and completion.band_public_key != self.expected_band_point
            ):
                raise EnrollmentError(
                    "Server band public key differs from the explicitly supplied key"
                )
            self._band_key = load_point(point) if point is not None else None
            self.stage, kind = "finish", 0x02002004
        else:
            raise EnrollmentError("Enrollment is not waiting for an HTTP result", stage=self.stage)
        return frame(
            0x8002,
            (kind,),
            field(1, completion.signature) + field(2, completion.receipt.encode("utf-8")),
        )
