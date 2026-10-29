"""No Bluetooth scanning, Keychain writes, UI, or ownership changes in this module."""

import sys

import pytest

from meta_neural_py.errors import DeviceBusy
from meta_neural_py.transport.lock import DeviceLock
from meta_neural_py.transport.macos import Helper


async def test_native_worker_imports_and_exits_on_parent_eof():
    if sys.platform != "darwin":
        pytest.skip("macOS helper smoke check")
    async with Helper("bluetooth") as helper:
        process = helper.process
    assert process.returncode == 0


def test_per_device_lock_contention_and_release(tmp_path):
    first = DeviceLock("device", tmp_path)
    second = DeviceLock("DEVICE", tmp_path)
    other = DeviceLock("different", tmp_path)
    first.acquire()
    with pytest.raises(DeviceBusy):
        second.acquire()
    other.acquire()
    first.release()
    second.acquire()
    second.release()
    other.release()


@pytest.mark.parametrize("short", ["FEB8", "180F", "2A19"])
def test_bluetooth_base_uuid_short_and_expanded_forms_match(short):
    from meta_neural_py.transport.base import bluetooth_uuid

    expanded = f"0000{short}-0000-1000-8000-00805F9B34FB"
    assert bluetooth_uuid(short) == bluetooth_uuid(expanded)
    assert bluetooth_uuid(expanded.lower()) == bluetooth_uuid(short)
