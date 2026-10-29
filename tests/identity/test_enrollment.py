import base64
import hashlib
import json

import pytest
from conftest import DEVICE_ID, Peer
from cryptography.hazmat.primitives.asymmetric import ec

from meta_neural_py import Band
from meta_neural_py.errors import EnrollmentError, IdentityStoreError, TrustVerificationFailed
from meta_neural_py.identity.enrollment import OwnershipCeremony, PairCompletion
from meta_neural_py.identity.meta_auth import MetaAuth, callback_blob
from meta_neural_py.identity.meta_pair import MetaPairClient, MetaSession
from meta_neural_py.identity.store import (
    MemoryIdentityStore,
    PendingEnrollment,
    decode_record,
    encode_record,
    import_kinesis_record,
)
from meta_neural_py.identity.trust import digest, load_point, public_point, sign, verify
from meta_neural_py.protocol.datax import DataXReceiver
from meta_neural_py.protocol.protobuf import Fields, field


def test_trust_uses_prehashed_digest_and_raw_signatures():
    key = ec.generate_private_key(ec.SECP256R1())
    transcript = digest(bytes(16), bytes(64), bytes(32), bytes(64))
    signature = sign(key, transcript)
    assert len(signature) == 64
    verify(key.public_key(), signature, transcript)
    with pytest.raises(TrustVerificationFailed):
        verify(key.public_key(), signature, bytes(32))


def test_identity_import_validates_both_host_keys_and_band_point():
    host, band = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())

    def b64(data):
        return base64.b64encode(data).decode()

    record = {
        "AppPrivateKey": b64(host.private_numbers().private_value.to_bytes(32, "big")),
        "AppPublicKey": b64(public_point(host.public_key())),
        "AppECPubicKey": b64(public_point(band.public_key())),
    }
    assert public_point(
        import_kinesis_record(json.dumps(record).encode()).band_public_key
    ) == public_point(band.public_key())
    record["AppPublicKey"] = b64(public_point(band.public_key()))
    with pytest.raises(IdentityStoreError):
        import_kinesis_record(json.dumps(record).encode())


def test_saved_records_validate_shape_and_do_not_leak_secrets():
    key = ec.generate_private_key(ec.SECP256R1())
    raw = encode_record(key, public_point(key.public_key()), "verified")
    assert decode_record(raw).stage == "verified"
    assert "private_key" not in repr(PendingEnrollment(key, "claim"))
    with pytest.raises(IdentityStoreError) as caught:
        decode_record(b'{"private_key":"TOP_SECRET"}')
    assert "TOP_SECRET" not in str(caught.value)


def test_ownership_ceremony_rejects_bad_order_and_missing_band_key():
    ceremony = OwnershipCeremony()
    with pytest.raises(EnrollmentError):
        ceremony.receive(0x02002005, b"")
    ceremony.receive(0x02003001, field(1, bytes(300)) + field(2, b"serial") + field(5, bytes(300)))
    request = ceremony.receive(0x02002001, field(1, bytes(16)))
    assert len(request.app_public_key) == 64
    pending = ' {"a": 1}\n'
    wire = ceremony.resume(PairCompletion(b"signature", pending))
    parsed = DataXReceiver().feed(wire, padded=False)[0]
    assert Fields(parsed.payload).bytes(2) == pending.encode()
    ceremony.receive(0x02002003, field(1, b"signature") + field(2, b"device receipt"))
    with pytest.raises(EnrollmentError, match="omitted band public key"):
        ceremony.resume(PairCompletion(b"signature", "final"))


@pytest.mark.parametrize(
    "preexisting_public_key,allow_missing", [(False, False), (True, False), (False, True)]
)
async def test_full_enrollment_reconnects_before_saving(
    tmp_path, preexisting_public_key, allow_missing
):
    store = MemoryIdentityStore()
    first = Peer(enrollment=True)
    second = Peer(band_key=first.band_key)

    class PairClient:
        async def pair_request(self, request):
            first.app_public_key = second.app_public_key = load_point(request.app_public_key)
            return PairCompletion(b"server signature", ' {"pending": 1} ')

        async def pair(self, request):
            assert request.receipt == ' {"device": 2} '
            assert store.load(DEVICE_ID) is None
            return PairCompletion(
                b"final signature",
                '\n{"final": 3}\n',
                None
                if preexisting_public_key or allow_missing
                else public_point(first.band_key.public_key()),
            )

    peers = iter([first, second])
    identity = await Band.enroll(
        DEVICE_ID,
        identity_store=store,
        pair_client=PairClient(),
        transport_factory=lambda: next(peers),
        lock_directory=tmp_path,
        allow_missing_band_key=allow_missing,
        band_public_key=public_point(first.band_key.public_key())
        if preexisting_public_key
        else None,
    )
    assert store.load(DEVICE_ID) is identity and store.load_pending(DEVICE_ID) is None
    assert first.closed and second.closed
    assert any(p.channel == 0x8003 for p in second.requests)
    assert (identity.band_public_key is None) == allow_missing
    assert identity.allow_missing_band_key == allow_missing


async def test_interrupted_enrollment_retains_pending_and_previous_identity(tmp_path):
    peer = Peer(enrollment=True)
    store = MemoryIdentityStore()
    old = peer.identity
    store.save(DEVICE_ID, old)

    class PairClient:
        async def pair_request(self, request):
            raise EnrollmentError("Synthetic HTTP failure")

    with pytest.raises(EnrollmentError):
        await Band.enroll(
            DEVICE_ID,
            identity_store=store,
            pair_client=PairClient(),
            transport_factory=lambda: peer,
            lock_directory=tmp_path,
        )
    assert store.load(DEVICE_ID) is old and store.load_pending(DEVICE_ID).stage == "identity"
    assert peer.closed


@pytest.mark.parametrize("scheme", ["fb-viewapp", "oculus"])
def test_callback_validation_is_exact_and_correlated(scheme):
    token = hashlib.sha256(b"native").hexdigest()[:16]
    callback = f"{scheme}://frl_login/?token={token}&blob=abc%2B123"
    assert callback_blob(callback, "native") == "abc+123"
    for invalid in (
        callback.replace(scheme, "https"),
        callback + "&token=x",
        callback.replace(token, "0" * 16),
    ):
        with pytest.raises(EnrollmentError):
            callback_blob(invalid, "native")


async def test_injected_login_and_http_auth_chain():
    calls = []

    class HTTP:
        async def post(self, url, fields, **kwargs):
            calls.append((url, fields, kwargs))
            if url.endswith("tokens_query"):
                return {"native_sso_token": "native", "native_sso_etoken": "escaped + value"}
            if url.endswith("blobs_decrypt"):
                assert fields["blob"] == "encrypted+blob"
                return {"access_token": "frl-secret"}
            assert fields["frl_access_token"] == "frl-secret"
            return {"access_token": "ar-secret", "user_id": 123}

    class Login:
        async def open(self, url):
            assert "escaped+%2B+value" in url
            token = hashlib.sha256(b"native").hexdigest()[:16]
            return f"fb-viewapp://frl_login?token={token}&blob=encrypted%2Bblob"

    session = await MetaAuth(Login(), HTTP()).authenticate()
    assert session.access_token == "ar-secret" and session.user_id == "123"
    assert "ar-secret" not in repr(session)
    assert len(calls) == 3


async def test_pair_http_preserves_receipts_and_finds_band_key_in_receipt():
    point = public_point(ec.generate_private_key(ec.SECP256R1()).public_key())
    receipt = (
        "\n"
        + json.dumps(
            {"additional_data": {"device_ec_public_key": base64.b64encode(point).decode()}}
        )
        + "\n"
    )

    class HTTP:
        async def post(self, url, fields, **kwargs):
            assert fields["device_pending_ownership_receipt"] == " exact device receipt\n"
            return {
                "final_ownership_receipt": receipt,
                "receipt_signature": base64.b64encode(b"sig").decode(),
            }

    from meta_neural_py.identity.enrollment import Pair

    result = await MetaPairClient(MetaSession("token", "user"), HTTP()).pair(
        Pair(" exact device receipt\n", b"sig")
    )
    assert result.receipt == receipt and result.band_public_key == point


def test_enrollment_rejects_server_key_that_differs_from_explicit_pin():
    first = public_point(ec.generate_private_key(ec.SECP256R1()).public_key())
    second = public_point(ec.generate_private_key(ec.SECP256R1()).public_key())
    ceremony = OwnershipCeremony(first)
    ceremony.receive(0x02003001, field(1, bytes(300)) + field(2, b"serial") + field(5, bytes(300)))
    ceremony.receive(0x02002001, field(1, bytes(16)))
    ceremony.resume(PairCompletion(b"signature", "pending"))
    ceremony.receive(0x02002003, field(1, b"signature") + field(2, b"device receipt"))
    with pytest.raises(EnrollmentError, match="differs"):
        ceremony.resume(PairCompletion(b"signature", "final", second))
    assert ceremony.stage == "pair"
