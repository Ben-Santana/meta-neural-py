"""Lazy Security.framework adapter; atomic updates preserve an existing identity."""

import sys

from ..errors import IdentityStoreError, UnsupportedPlatform
from .store import Identity, PendingEnrollment, decode_record, encode_record
from .trust import load_point, public_point


class KeychainIdentityStore:
    service = "org.meta-neural-py.identity.v1"

    @staticmethod
    def _security():
        if sys.platform != "darwin":
            raise UnsupportedPlatform("Default identity storage requires macOS Keychain")
        try:
            import Security

            return Security
        except ImportError:
            raise IdentityStoreError(
                "Install pyobjc-framework-Security for Keychain storage"
            ) from None

    def _query(self, account):
        s = self._security()
        return s, {
            s.kSecClass: s.kSecClassGenericPassword,
            s.kSecAttrService: self.service,
            s.kSecAttrAccount: account,
        }

    def _read(self, account):
        s, query = self._query(account)
        query.update({s.kSecReturnData: True, s.kSecMatchLimit: s.kSecMatchLimitOne})
        status, data = s.SecItemCopyMatching(query, None)
        if status == s.errSecItemNotFound:
            return None
        if status != s.errSecSuccess:
            raise IdentityStoreError(f"Keychain read denied or unavailable (OSStatus {status})")
        return bytes(data)

    def _write(self, account, data):
        s, query = self._query(account)
        status = s.SecItemUpdate(query, {s.kSecValueData: data})
        if status == s.errSecItemNotFound:
            query.update(
                {
                    s.kSecValueData: data,
                    s.kSecAttrAccessible: s.kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                }
            )
            status, _ = s.SecItemAdd(query, None)
        if status != s.errSecSuccess:
            raise IdentityStoreError(f"Keychain write denied or unavailable (OSStatus {status})")

    def load(self, device_id):
        raw = self._read(device_id)
        if raw is None:
            return None
        record = decode_record(raw)
        if record.stage not in ("verified", "host_verified"):
            raise IdentityStoreError("Saved identity is incomplete; inspect interrupted enrollment")
        if record.band_point is None and record.stage != "host_verified":
            raise IdentityStoreError("Verified record is missing its band public key")
        return Identity(
            record.private_key,
            load_point(record.band_point) if record.band_point else None,
            record.allow_missing_band_key,
        )

    def save(self, device_id, identity: Identity):
        self._write(
            device_id,
            encode_record(
                identity.private_key,
                public_point(identity.band_public_key) if identity.band_public_key else None,
                "verified" if identity.band_public_key else "host_verified",
                allow_missing_band_key=identity.allow_missing_band_key,
            ),
        )

    def load_pending(self, device_id):
        raw = self._read(device_id + ".pending")
        return decode_record(raw) if raw is not None else None

    def save_pending(self, device_id, pending: PendingEnrollment):
        self._write(
            device_id + ".pending",
            encode_record(
                pending.private_key,
                pending.band_point,
                pending.stage,
                allow_missing_band_key=pending.allow_missing_band_key,
            ),
        )

    def clear_pending(self, device_id):
        s, query = self._query(device_id + ".pending")
        status = s.SecItemDelete(query)
        if status not in (s.errSecSuccess, s.errSecItemNotFound):
            raise IdentityStoreError(f"Keychain delete denied (OSStatus {status})")
