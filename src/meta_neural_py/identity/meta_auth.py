import hashlib
import hmac
import secrets
import urllib.parse
import uuid
from typing import Protocol

from ..errors import EnrollmentError
from .meta_pair import (
    AR_CLIENT,
    FRL_CLIENT,
    FormHTTPClient,
    HTTPClient,
    MetaSession,
    _required_text,
)

CALLBACK_SCHEMES = ("fb-viewapp", "oculus")


class LoginProvider(Protocol):
    async def open(self, url: str) -> str:
        """Display Meta's login and return its exact frl_login custom-scheme callback."""
        ...


def callback_blob(url: str, native_token: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in CALLBACK_SCHEMES
        or parsed.netloc != "frl_login"
        or parsed.path not in ("", "/")
    ):
        raise EnrollmentError("Invalid Meta login callback destination")
    try:
        values = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    except ValueError:
        raise EnrollmentError("Malformed Meta login callback") from None
    expected = hashlib.sha256(native_token.encode()).hexdigest()[:16]
    token, blob = values.get("token", []), values.get("blob", [])
    if (
        len(token) != 1
        or len(blob) != 1
        or not blob[0]
        or not hmac.compare_digest(token[0], expected)
    ):
        raise EnrollmentError("Meta login callback did not match this sign-in")
    return blob[0]


class WebKitLoginProvider:
    async def open(self, url: str) -> str:
        from ..transport.macos import Helper

        async with Helper("login") as helper:
            await helper.send({"op": "login", "url": url})
            response = await helper.receive()
            if response["event"] != "callback":
                raise EnrollmentError("Login helper returned an unexpected response")
            return response["url"]


class MetaAuth:
    def __init__(self, login: LoginProvider | None = None, http: HTTPClient | None = None):
        self.login, self.http = login or WebKitLoginProvider(), http or FormHTTPClient()

    @staticmethod
    def _lsd():
        lsd = "S0." + "".join(str(secrets.randbelow(10)) for _ in range(6))
        return {"lsd": lsd, "jazoest": "2" + str(sum(map(ord, lsd)))}

    async def authenticate(self) -> MetaSession:
        result = await self.http.post(
            "https://meta.graph.meta.com/webview_tokens_query",
            {"access_token": FRL_CLIENT, **self._lsd()},
            headers={"Origin": "https://auth.meta.com"},
            multipart=True,
        )
        native = _required_text(result, "native_sso_token")
        etoken = _required_text(result, "native_sso_etoken")
        url = "https://auth.meta.com/?" + urllib.parse.urlencode(
            {
                "native_app_id": "388177446008673",
                "source_app_id": "388177446008673",
                "native_sso_etoken": etoken,
            }
        )
        callback = await self.login.open(url)
        blob = callback_blob(callback, native)
        result = await self.http.post(
            "https://meta.graph.meta.com/webview_blobs_decrypt",
            {"blob": blob, "request_token": native, "access_token": FRL_CLIENT, **self._lsd()},
            headers={"Origin": "https://auth.meta.com"},
            multipart=True,
        )
        frl_token = _required_text(result, "access_token")
        result = await self.http.post(
            "https://ar-genai.graph.meta.com/login",
            {
                "frl_access_token": frl_token,
                "logging_session_id": str(uuid.uuid4()),
                "format": "json",
                "device_id": str(uuid.uuid4()),
                "generate_session_cookies": "1",
                "generate_analytics_claim": "1",
                "method": "POST",
            },
            headers={"Authorization": "OAuth " + AR_CLIENT},
        )
        user = result.get("user_id")
        if not isinstance(user, (str, int)) or isinstance(user, bool) or not str(user):
            raise EnrollmentError("Meta did not return a user identity")
        return MetaSession(_required_text(result, "access_token"), str(user))
