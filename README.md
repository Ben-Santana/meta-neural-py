# meta-neural-py

Experimental Python access to the Meta Neural Band: gestures, motion, EMG,
and sensor-only recordings, with explicit Meta enrollment and direct Bluetooth.
No Kinesis installation or runtime Xcode dependency is required.

**Development alpha (`0.1.0a1`).** One band has been smoke-tested on macOS 26.5.2
with Python 3.12.13 using compatibility enrollment. Sustained throughput, other
firmware/hosts, and the fully verified live enrollment path remain unvalidated.
See [validation and limitations](docs/validation.md).

## Install

Requires Python 3.11+. Live Bluetooth and default identity storage require macOS.
Linux and Windows can use offline protocol and replay functionality only.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install meta-neural-py==0.1.0a1
```

For optional NumPy conversion, install `meta-neural-py[numpy]==0.1.0a1`.

## Set up your band once

Put the intended band in pairing mode, close other band clients, and allow macOS
Bluetooth access when prompted. Select its UUID explicitly:

```sh
meta-neural discover --timeout 5
meta-neural enroll DEVICE_UUID
meta-neural status DEVICE_UUID
meta-neural gestures DEVICE_UUID
```

Enrollment opens Meta sign-in and **changes band ownership**. The default requires
a band public key and verifies its proof. If Meta omits that key, strict enrollment
fails. The tested standalone alternative is an explicit choice:

```sh
meta-neural enroll DEVICE_UUID --allow-missing-band-key
```

This still requires the band to accept a fresh host signing identity, but cannot
verify the band's own proof without its public key. The policy persists and
`band.band_verified` remains `False`. Available keys are always verified.
See [setup and trust](docs/setup.md) before choosing this option.

Enrollment saves a per-band identity in your macOS Keychain, outside your venv.
**Two projects on the same Mac under the same macOS user share that identity**
when using the default store: install in each venv, enroll once, then connect from
either project. macOS may request access for a different Python host. Only one
client can connect at a time. A different Mac/user needs its own setup. Meta
account tokens stay in memory; your password is handled by Meta's sign-in page.

## Use Python

After enrollment, replace `DEVICE_UUID` with the UUID from discovery:

```python
import asyncio

from meta_neural_py import Band, Gesture, Streams


async def main():
    async with Band("DEVICE_UUID") as band:
        async with band.subscribe(Streams(gestures=True)) as events:
            async for event in events:
                if isinstance(event, Gesture):
                    print(event.finger, event.action)


asyncio.run(main())
```

Stop with Ctrl-C. Context managers acknowledge stream shutdown and release the
connection. Connection and reconnect never enroll automatically.

## Learn and contribute

[Documentation](docs/index.md) ·
[Report a bug](https://github.com/Ben-Santana/meta-neural-py/issues)

- [Getting started](docs/getting-started.md) and [usage](docs/usage.md)
- [Public API](docs/api.md) and [data format](docs/data-format.md)
- [Troubleshooting](docs/troubleshooting.md) and [architecture](docs/setup.md)
- [Contributing](CONTRIBUTING.md), [hardware testing](docs/hardware-testing.md),
  and [release validation](docs/validation.md)

MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Based on the MIT
[callbacked/kinesis reference](https://github.com/callbacked/kinesis/tree/5c78485f48c0758e2ce46ee11d3aaa070d827b75).
Not affiliated with Meta. No account tokens, identities, real recordings,
firmware, or model binaries are distributed.
