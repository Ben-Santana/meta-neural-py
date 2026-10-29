# Validation and release status

Version `0.1.0a1` was published as an experimental prerelease on October 29, 2026.
Both PyPI artifact hashes matched the reviewed local distributions, and the release
installed successfully from PyPI in a clean virtual environment with a working CLI.
Published artifacts were not rebuilt or changed after upload.

## Live hardware smoke tests — October 29, 2026

One band was tested on macOS 26.5.2 with Python 3.12.13:

- Discovery, service/PSM discovery, native L2CAP open/close, and per-device lock release.
- Interactive Meta sign-in with the actual `oculus://frl_login/` callback.
- Standalone HTTP/BLE enrollment using this package's fresh signing identity and
  its own Keychain namespace. No Kinesis identity or key was imported.
- Reconnection with the saved identity, device-info acknowledgement, right-hand
  configuration read, and battery status read.
- A 30-second gesture test received 218 gesture events, including index/middle
  taps/holds and thumb directional events.
- A 15-second motion test received 1,629 gyro and 1,629 orientation events, plus
  gesture events.
- A 15-second EMG test yielded 1,642 batches during iteration. Total decoded
  traffic, including batches received during shutdown, contained 26,496 sample
  frames. The 2048 Hz / 8-channel / 16-bit / 16-sample / encoding-0 configuration
  passed validation, and each payload decoded as 16 by 8 unsigned ADC counts.
- All three sensor tests reported zero local queue drops and acknowledged stream
  shutdown. Each released the native transport and device lock.

Meta omitted the band public key from its ownership response. These live sessions
used the explicitly approved `allow_missing_band_key=True` compatibility policy:
the host signing identity was accepted, but the band proof was NOT independently
verified. `band.band_verified` reported `False`. Strict verification remains the
default, and an available band key is always checked even in compatibility mode.

Observed gesture counter jumps cannot yet be mapped to packet loss. An earlier
inferred-gap calculation overcounted them and has been removed. Device packet loss
is unknown (`sequence_gaps=None`); raw counter discontinuities and local queue
drops are reported separately. No physical axis, ADC voltage, or firmware-version
claim follows from these tests. One successful setup does not establish support
for every band firmware or host version.

The live tests exposed two native integration issues now fixed: expanded versus
short Bluetooth-base UUID matching, and a PyObjC navigation-decision block lacking
a runtime signature. Login redirect capture now uses WKURLSchemeHandler for both
`fb-viewapp` and `oculus`, avoiding that block callback.

## Offline/native regression checks

The 87 offline tests cover independently computed HKDF/AES/HMAC vectors,
fragmented/coalesced records, counter wrap, MAC failures, protobuf checks, channel
continuations and RPC correlation/deadlines, reordered/invalid/duplicate proofs,
strict and explicit compatibility enrollment, Keychain policy persistence,
interrupted setup, exact receipts, stream flags, bounded queues, hand readback,
optional battery failures, cancellation, cleanup, and reconnect discontinuities.

Two additional opt-in hidden native WebKit tests capture fixed synthetic redirects
and exit normally without visible windows, network requests, credentials, Bluetooth,
or ownership changes:

```sh
META_NEURAL_WEBKIT_TEST=1 pytest tests/hardware/test_webkit.py
```

Run `pytest`, `ruff check src tests examples`, `ruff format --check src tests examples`, and `mypy src/meta_neural_py`. Build with
`python -m build` and validate metadata with `twine check --strict dist/*`.
Wheel/source installations are checked outside the repository in fresh virtual
environments. CI covers offline checks on Python 3.11–3.13 on Linux/macOS;
ownership changes and hardware tests never run in ordinary CI.

## Still outstanding

- A 30-minute sustained session and throughput/latency measurements.
- Real device packet-loss semantics, mixed EMG/motion throughput, and known-signal
  ADC validation beyond supported layout and numeric decoding.
- Actual disconnect recovery, reconnect after a band restart, and live Ctrl-C cases.
- Independent left/right write/readback on hardware; the live test read right-hand
  state without changing the selected hand.
- Other macOS/Python/firmware combinations, notebooks and GUI hosts, physical axes,
  quaternion coordinate conventions, and the fully verified live band-key path.

## Provenance

Reference: callbacked/kinesis commit
`5c78485f48c0758e2ce46ee11d3aaa070d827b75`, MIT, copyright 2026 callbacked.
The notice and full license are included in both distributions. The unlicensed
PoC implementation was not copied. No real captures, identity records, account
tokens, or signed ownership receipts are distributed.
