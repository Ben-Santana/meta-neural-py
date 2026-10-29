import hashlib
import os
import sys
from pathlib import Path

from ..errors import DeviceBusy, UnsupportedPlatform


class DeviceLock:
    """Advisory, per-device OS lock. Files remain to avoid unlink/inode races."""

    def __init__(self, device_id: str, directory: Path | None = None):
        if directory is None:
            base = Path.home() / (
                "Library/Application Support" if sys.platform == "darwin" else ".local/share"
            )
            directory = base / "meta-neural-py" / "locks"
        self.path = directory / (hashlib.sha256(device_id.lower().encode()).hexdigest() + ".lock")
        self._fd: int | None = None

    def acquire(self):
        if os.name == "nt":
            raise UnsupportedPlatform("Native band access is not implemented on Windows")
        if self._fd is not None:
            raise DeviceBusy("This device lock is already held")
        import fcntl

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise DeviceBusy(
                "Another meta-neural-py client owns this device; close it first"
            ) from None
        self._fd = fd

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
