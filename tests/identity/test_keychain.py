import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from meta_neural_py.errors import IdentityStoreError
from meta_neural_py.identity.keychain import KeychainIdentityStore
from meta_neural_py.identity.store import Identity, PendingEnrollment


class FakeSecurity:
    """A stateful API double; tests never read or write the real Keychain."""

    kSecClass = "class"
    kSecClassGenericPassword = "generic"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecReturnData = "return"
    kSecMatchLimit = "limit"
    kSecMatchLimitOne = "one"
    kSecValueData = "data"
    kSecAttrAccessible = "accessible"
    kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly = "device"
    errSecItemNotFound = -25300
    errSecSuccess = 0

    def __init__(self):
        self.items = {}
        self.denied = False

    def SecItemCopyMatching(self, query, result):
        if self.denied:
            return -25293, None
        value = self.items.get(query["account"])
        return (0, value) if value is not None else (-25300, None)

    def SecItemUpdate(self, query, changes):
        if self.denied:
            return -25293
        if query["account"] not in self.items:
            return -25300
        self.items[query["account"]] = changes["data"]
        return 0

    def SecItemAdd(self, query, result):
        self.items[query["account"]] = query["data"]
        return 0, None

    def SecItemDelete(self, query):
        self.items.pop(query["account"], None)
        return 0


def test_keychain_round_trip_and_pending_separation(monkeypatch):
    api = FakeSecurity()
    store = KeychainIdentityStore()
    monkeypatch.setattr(store, "_security", lambda: api)
    host, band = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    identity = Identity(host, band.public_key())
    assert store.load("device") is None
    store.save("device", identity)
    loaded = store.load("device")
    assert loaded.private_key.private_numbers() == host.private_numbers()
    store.save_pending("device", PendingEnrollment(host, "claim"))
    assert store.load_pending("device").stage == "claim"
    assert store.load("device").private_key.private_numbers() == host.private_numbers()
    store.clear_pending("device")
    assert store.load_pending("device") is None


def test_denied_access_is_distinct_from_missing_and_preserves_item(monkeypatch):
    api = FakeSecurity()
    api.items["device"] = b"existing item"
    api.denied = True
    store = KeychainIdentityStore()
    monkeypatch.setattr(store, "_security", lambda: api)
    with pytest.raises(IdentityStoreError, match="denied"):
        store.load("device")
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(IdentityStoreError, match="denied"):
        store.save("device", Identity(key, key.public_key()))
    assert api.items["device"] == b"existing item"


def test_explicit_host_only_identity_persists_without_claiming_band_verification(monkeypatch):
    import json

    from meta_neural_py.identity.store import decode_record

    api = FakeSecurity()
    store = KeychainIdentityStore()
    monkeypatch.setattr(store, "_security", lambda: api)
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(IdentityStoreError, match="required"):
        Identity(key, None)
    store.save("device", Identity(key, None, allow_missing_band_key=True))
    record = decode_record(api.items["device"])
    assert record.stage == "host_verified" and record.allow_missing_band_key
    loaded = store.load("device")
    assert loaded.band_public_key is None and loaded.allow_missing_band_key
    raw = json.loads(api.items["device"])
    raw.pop("allow_missing_band_key")
    api.items["device"] = json.dumps(raw).encode()
    with pytest.raises(IdentityStoreError):
        store.load("device")
