"""Isolated reverse-engineered service endpoints; no HTTP bodies reach errors."""

import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from ..errors import EnrollmentError
from .enrollment import Pair, PairCompletion, PairRequest

FRL_CLIENT = "FRL|388177446008673|083800dd7efbbd42eab18c9886d79c18"
AR_CLIENT = "AR|306760944872162|a919421a55a8ea18080ab2f10f57f1be"
HW_CLIENT = "HW|1312539125771114|98588f106d5d542adbf590619ca071fe"


@dataclass(frozen=True)
class MetaSession:
    access_token: str = field(repr=False)
    user_id: str

    def __post_init__(self):
        if not self.access_token or not self.user_id:
            raise EnrollmentError("Meta session is incomplete")


class HTTPClient(Protocol):
    async def post(
        self,
        url: str,
        fields: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
        multipart: bool = False,
    ) -> dict: ...


class PairClient(Protocol):
    async def pair_request(self, request: PairRequest) -> PairCompletion: ...
    async def pair(self, request: Pair) -> PairCompletion: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EnrollmentError("Unexpected redirect from authentication service")


class FormHTTPClient:
    async def post(self, url, fields, *, headers=None, multipart=False):
        # Each blocking request has its own bounded timeout. Cancellation never sends a BLE step.
        return await asyncio.to_thread(self._post, url, fields, headers or {}, multipart)

    @staticmethod
    def _post(url, fields, headers, multipart):
        import uuid

        if multipart:
            boundary = "meta-neural-" + uuid.uuid4().hex
            body = b"".join(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                    + v
                    + "\r\n"
                ).encode()
                for k, v in fields.items()
            )
            body += f"--{boundary}--\r\n".encode()
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            body = urllib.parse.urlencode(fields).encode()
            content_type = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": content_type, "Accept": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
                data = response.read(1_048_577)
                if len(data) > 1_048_576:
                    raise EnrollmentError("Authentication response exceeded size limit")
                result = json.loads(data)
        except urllib.error.HTTPError as exc:
            raise EnrollmentError(f"Authentication service HTTP {exc.code}") from None
        except (urllib.error.URLError, ValueError, OSError):
            raise EnrollmentError(
                "Authentication service unavailable or response invalid"
            ) from None
        if not isinstance(result, dict):
            raise EnrollmentError("Invalid authentication response")
        if "error" in result:
            error = result["error"]
            code = error.get("code") if isinstance(error, dict) else None
            safe_code = code if type(code) is int else None
            raise EnrollmentError("Authentication service rejected request", code=safe_code)
        return result


def _required_text(result, key):
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise EnrollmentError("Authentication service omitted a required field")
    return value


def _signature(result):
    try:
        value = base64.b64decode(_required_text(result, "receipt_signature"), validate=True)
    except ValueError:
        raise EnrollmentError("Invalid receipt signature encoding") from None
    if not value:
        raise EnrollmentError("Empty receipt signature")
    return value


class MetaPairClient:
    def __init__(self, session: MetaSession, http: HTTPClient | None = None):
        self.session, self.http = session, http or FormHTTPClient()

    def _common(self):
        return {
            "access_token": HW_CLIENT,
            "user_access_token": self.session.access_token,
            "user_token_universe": "ar",
            "pair_protocol_version": "3",
            "method": "post",
        }

    async def pair_request(self, request: PairRequest) -> PairCompletion:
        def b64(data):
            return base64.b64encode(data).decode("ascii")

        fields = {
            **self._common(),
            "device_cert": b64(request.certificate),
            "serial_number": request.serial,
            "additional_data": json.dumps(
                {
                    "device_nonce": b64(request.nonce),
                    "app_pubkey": b64(request.app_public_key),
                    "secondary_cert": b64(request.secondary_certificate),
                },
                separators=(",", ":"),
            ),
        }
        result = await self.http.post("https://graph.facebook-hardware.com/pair_request", fields)
        return PairCompletion(
            _signature(result), _required_text(result, "pending_ownership_receipt")
        )

    async def pair(self, request: Pair) -> PairCompletion:
        result = await self.http.post(
            "https://graph.facebook-hardware.com/pair",
            {
                **self._common(),
                "device_pending_ownership_receipt": request.receipt,
                "device_pending_ownership_receipt_signature": base64.b64encode(
                    request.signature
                ).decode(),
            },
        )
        receipt = _required_text(result, "final_ownership_receipt")
        point = self._point(result)
        if point is None:
            try:
                point = self._point(json.loads(receipt))
            except (ValueError, TypeError):
                pass
        return PairCompletion(_signature(result), receipt, point)

    @staticmethod
    def _point(result):
        if not isinstance(result, dict) or not isinstance(result.get("additional_data"), dict):
            return None
        value = result["additional_data"].get("device_ec_public_key")
        if value is None:
            return None
        try:
            point = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise EnrollmentError("Invalid band key encoding") from None
        if len(point) != 64:
            raise EnrollmentError("Band key is not a raw P-256 point")
        return point
