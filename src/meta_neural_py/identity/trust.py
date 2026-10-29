import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from ..errors import TrustVerificationFailed


def public_point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )[1:]


def load_point(point: bytes) -> ec.EllipticCurvePublicKey:
    if len(point) != 64:
        raise TrustVerificationFailed("Expected a raw P-256 public point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + point)
    except ValueError:
        raise TrustVerificationFailed("Invalid P-256 public point") from None


def digest(challenge: bytes, receiver: bytes, seed: bytes, sender: bytes) -> bytes:
    if (len(challenge), len(receiver), len(seed), len(sender)) != (16, 64, 32, 64):
        raise TrustVerificationFailed("Invalid trust transcript dimensions")
    return hashlib.sha256(
        hashlib.sha256(challenge + receiver).digest() + hashlib.sha256(seed + sender).digest()
    ).digest()


def sign(key: ec.EllipticCurvePrivateKey, transcript: bytes) -> bytes:
    signature = key.sign(transcript, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    r, s = utils.decode_dss_signature(signature)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify(key: ec.EllipticCurvePublicKey, signature: bytes, transcript: bytes):
    if len(signature) != 64:
        raise TrustVerificationFailed("Invalid band proof length")
    der = utils.encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    try:
        key.verify(der, transcript, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    except (InvalidSignature, ValueError):
        raise TrustVerificationFailed("Band proof does not match saved band key") from None
