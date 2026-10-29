# Getting started

## Install

Use Python 3.11 or newer on macOS for live Bluetooth. No Kinesis app, Swift
executable, or runtime Xcode installation is required. Other platforms support
offline protocol and replay only.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install meta-neural-py==0.1.0a1
meta-neural --help
```

## Discover and enroll

Close other band clients. Put your band in pairing mode and accept the macOS
Bluetooth prompt. Discover, then substitute the selected UUID in subsequent commands:

```sh
meta-neural discover --timeout 5
meta-neural enroll DEVICE_UUID
```

The enrollment command opens Meta sign-in and changes band ownership. Your
password stays in Meta's sign-in page. Account tokens are held in memory only.
Strict enrollment requires a band public key and validates its proof.

Meta omitted that key during the recorded hardware test. If you deliberately
accept an unverified band proof, use the explicit compatibility option:

```sh
meta-neural enroll DEVICE_UUID --allow-missing-band-key
```

The band must still accept the new host identity. An available band key is always
checked. This policy is saved, and `band.band_verified` is `False` without a key.
See [setup and trust](setup.md) for known-key enrollment and interrupted attempts.

## Check and connect

```sh
meta-neural status DEVICE_UUID
meta-neural gestures DEVICE_UUID
meta-neural test DEVICE_UUID --duration 60 --profile gestures
```

`status` inspects local setup without Bluetooth. Stop streaming with Ctrl-C.
For Python programs, continue to [usage](usage.md).

## Two projects or virtual environments

The default identity store uses macOS Keychain service
`org.meta-neural-py.identity.v1`, keyed by band UUID. It is outside the package,
project, and venv. On the same Mac under the same macOS user, enroll once in
project A, then install the package in project B and connect using the same UUID.
Do not enroll again just because you created another venv.

Only one client can own the band's connection at a time. Different Python hosts
may trigger macOS Keychain/Bluetooth permissions. Different Macs/users need
separate setup. Custom stores may isolate identities; `MemoryIdentityStore` loses
its contents when the process exits. Band resets or ownership changes elsewhere
can invalidate a saved identity; reconnect does not silently replace it.

See [troubleshooting](troubleshooting.md) if setup fails and
[validation](validation.md) for the tested host and outstanding limitations.
