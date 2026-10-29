"""Errors carry context, never wire payloads or credential material."""


class BandError(Exception):
    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        stage: str | None = None,
        channel: int | None = None,
        code: int | None = None,
    ):
        self.operation, self.stage, self.channel, self.code = operation, stage, channel, code
        context = ", ".join(
            f"{k}={v}"
            for k, v in (
                ("operation", operation),
                ("stage", stage),
                ("channel", f"0x{channel:04x}" if channel is not None else None),
                ("code", f"0x{code:08x}" if code is not None else None),
            )
            if v is not None
        )
        super().__init__(f"{message} ({context})" if context else message)


class ProtocolError(BandError):
    pass


class AuthenticationError(ProtocolError):
    pass


class ServiceUnavailable(BandError):
    pass


class IdentityMismatch(BandError):
    pass


class TrustVerificationFailed(AuthenticationError):
    pass


class RequestTimeout(BandError):
    pass


class UnsupportedConfiguration(BandError):
    pass


class UnsupportedPlatform(BandError):
    pass


class DeviceBusy(BandError):
    pass


class DeviceDisconnected(BandError):
    pass


class StreamShutdownError(BandError):
    pass


class QueueOverflow(BandError):
    pass


class IdentityStoreError(BandError):
    pass


class EnrollmentError(BandError):
    pass


class LoginCancelled(EnrollmentError):
    pass


class PermissionDenied(BandError):
    pass


def result_error(code: int, *, operation: str, stage: str, channel: int) -> BandError:
    cls: type[BandError] = BandError
    message = "Band rejected request"
    if code == 0x0300C001:
        cls, message = (
            ServiceUnavailable,
            "Band service unavailable; verify enrollment and session trust",
        )
    elif code == 0x03001043:
        cls, message = (
            IdentityMismatch,
            "Band rejected saved identity; explicitly enroll to change ownership",
        )
    elif code == 0x03001044:
        cls, message = TrustVerificationFailed, "Band rejected signature"
    return cls(message, operation=operation, stage=stage, channel=channel, code=code)
