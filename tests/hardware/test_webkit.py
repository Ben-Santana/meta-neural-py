"""Opt-in hidden WebKit check. No UI, account data, Bluetooth, or ownership changes."""

import asyncio
import os
import sys

import pytest

from meta_neural_py.transport.macos import Helper


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("META_NEURAL_WEBKIT_TEST") != "1",
    reason="Opt-in native WebKit process check requires macOS host access",
)
@pytest.mark.parametrize("scheme", ["fb-viewapp", "oculus"])
async def test_hidden_custom_scheme_callback_and_clean_exit(scheme):
    async with Helper("login-smoke") as helper:
        await helper.send({"op": "login", "url": "https://auth.meta.com/", "test_scheme": scheme})
        async with asyncio.timeout(10):
            response = await helper.receive()
            assert response["event"] == "callback"
            assert response["url"] == scheme + "://frl_login/?token=synthetic&blob=synthetic"
            assert await helper.process.wait() == 0
