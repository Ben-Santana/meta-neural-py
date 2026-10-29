# meta_neural_py — project outline

Date: 2026-10-29. Status: design proposal; no package implementation yet.

## Goal

Build a pip-installable Python library that lets other projects discover a Meta Neural Band, enroll it, connect, and consume typed sensor events with a small API. Reproduce Kinesis's band data access without requiring its desktop app, UI, gesture-to-keyboard mappings, or a dashboard.

Proposed distribution: `meta-neural-py`; import: `meta_neural_py`. Check PyPI name availability before reserving the final distribution name. Start with macOS and Python 3.11+; validate supported macOS/Python versions on hardware before advertising them. Linux and Windows are future transport implementations, not initial compatibility promises.

“Access Kinesis's data” means connect directly to the band using equivalent protocol support. The reviewed Kinesis package exposes an app executable, not a supported Python interface or data server. Concurrent access while Kinesis owns the Bluetooth connection is outside the initial scope. A future local broker could fan one connection out to multiple applications.

## Code reviewed and implementation evidence

Local PoC: `/Users/ben/Desktop/Life/Developer/Projects/neural-band-poc`, commit `a3d217b736181783e6eacff6a1bacb7004092158`.

- `instrumentation/airshield.py`: directional key derivation, authenticated record decryption, counters, and buffered transport records. Useful core to extract; deliberately does not implement identity authentication.
- `instrumentation/extract_telemetry.py`: incremental DataX framing and checked protobuf fields. Separate reusable framing from telemetry/file-analysis routines.
- `instrumentation/mac_band_probe.py`: CoreBluetooth discovery, L2CAP I/O, encryption setup, stream RPCs, hand configuration, and bounded shutdown. This is a CLI-oriented prototype with a main-thread run loop, signal handlers, capture paths, and secret-bearing diagnostic output; do not expose it directly as the library API.
- `instrumentation/input_service.py`: recognized gestures, gyro, quaternion, configuration, experimental EMG batches and inference samples. Separate raw decoding from `PinchDial` interaction behavior.
- `instrumentation/band_access.py`: process lock currently tied to the repository's captures folder. Replace with a per-device application-support lock.
- Existing `test_airshield.py`, `test_extract_telemetry.py`, `test_mac_band_probe.py`, and `test_input_service.py`: starting points for portable fixtures and regression cases.
- `docs/enrollment-flow.md`, `docs/trust-handshake.md`, and `docs/handedness.md`: protocol reference material, not implemented enrollment support in the Python probe.

Kinesis: [callbacked/kinesis](https://github.com/callbacked/kinesis), reviewed at commit [`5c78485f48c0758e2ce46ee11d3aaa070d827b75`](https://github.com/callbacked/kinesis/tree/5c78485f48c0758e2ce46ee11d3aaa070d827b75).

- [`BandSession.swift`](https://github.com/callbacked/kinesis/blob/5c78485f48c0758e2ce46ee11d3aaa070d827b75/Sources/KinesisCore/BandSession.swift): enrollment/legacy startup branches, mutual trust, link completion, device-info gating, stream updates, hand verification, and rejection handling.
- [`BandWire.swift`](https://github.com/callbacked/kinesis/blob/5c78485f48c0758e2ce46ee11d3aaa070d827b75/Sources/KinesisCore/BandWire.swift) and `AirShield.swift`: framing, protobuf helpers, and encrypted transport reference implementations.
- [`OwnershipCeremony.swift`](https://github.com/callbacked/kinesis/blob/5c78485f48c0758e2ce46ee11d3aaa070d827b75/Sources/KinesisCore/OwnershipCeremony.swift): BLE enrollment state machine; emits requests for the app to perform the HTTP portions, then resumes from their results.
- [`BandIdentity.swift`](https://github.com/callbacked/kinesis/blob/5c78485f48c0758e2ce46ee11d3aaa070d827b75/Sources/KinesisCore/BandIdentity.swift): persistent signing identity and band public key in macOS Keychain; also validates imported identity records.
- `Sources/Kinesis/MetaAuth.swift`, `MetaLoginView.swift`, and `MetaPairClient.swift`: interactive Meta sign-in and ownership HTTP exchanges. These are app-side dependencies that must be replaced or adapted, not functionality supplied automatically by `KinesisCore`.
- [`BandConnection.swift`](https://github.com/callbacked/kinesis/blob/5c78485f48c0758e2ce46ee11d3aaa070d827b75/Sources/Kinesis/BandConnection.swift): event-driven CoreBluetooth/L2CAP on the main run loop, connection lifecycle, and local ownership locking.
- `Sources/KinesisCore/EMGReadings.swift`, `Gestures.swift`, `BandBatteryStatus.swift`, and `BandModelCapture.swift`: data models, config checks, battery handling, and experimental model capture.
- `Tests/KinesisCoreTests/EnrolledIdentityTests.swift`, `OwnershipCeremonyTests.swift`, and `BandSessionTests.swift`: behavioral references for startup ordering and failure cases.

## Immediate problem the package must solve

The current dashboard completes transport encryption but sends input requests before establishing the required session trust. In the October 29 capture, the band returned `0x0300c001` on device-info, hand-config, and streaming channels. The Python input parser ignored those terminal result words, so the hand request appeared to time out five seconds later.

This is evidence for implementing the enrolled startup path and immediate RPC rejection handling. It does not establish the band's ownership history from the log alone. The package should diagnose setup state instead of advising a longer timeout or silently resetting ownership.

Pairing the band with Kinesis alone will not add trust support to the Python probe. A new user must be able to enroll through this package before the first usable standalone release; importing an existing identity is an optional compatibility path.

## Initial data contract

- **Gestures:** sequence, source timestamp, receive timestamp, finger, raw action, derived action, synthetic flag, and original numeric codes. Preserve unknown enum values. Offer convenient labels without discarding raw events.
- **Gyroscope:** three signed little-endian int16 components, sequence, and timestamps. Publish wire values first. Physical units and anatomical axes need validation; any scaled values must name their experimental calibration.
- **Orientation:** four float32 quaternion components plus timestamps and sequence. Current Kinesis documents wire order as `w, x, y, z`; older PoC notes leave order unverified. Preserve raw component order and validate coordinate conventions before promising a calibrated orientation API.
- **Raw EMG:** configuration plus batches with 16 sample frames × 8 channels for the currently supported 2048 Hz / 16-bit / encoding-0 format. Payloads are 256 bytes of unsigned little-endian ADC counts. No voltage, electrode-location, or finger-joint claims. Reject unsupported layouts explicitly instead of guessing.
- **Device/status:** discovered identity, firmware/model when available, battery level, optional extended battery status, connection state, confirmed hand, active stream flags, and diagnostic counters. Optional battery-service failure must not terminate otherwise healthy sensor streams.
- **Experimental inference:** later opt-in module exposing pipeline, dimensions, sequence, timestamps, optional latency, finite float values, and original sensor payload. The PoC's nine-score decoder is narrower than current Kinesis's bounded variable-length decoder. Do not hardcode all pipelines to nine classes or promise handwriting recognition.

Every event includes a host monotonic receive time and a declared source clock domain. Keep device timestamps as integers; gesture and motion clocks must not be assumed interchangeable. Track device sequence gaps separately from local queue drops. Handle sequence resets/wraps and reconnect boundaries without claiming exact loss when continuity is unknown.

## Proposed public API

The following is a target interface, not executable code available today:

```python
import asyncio
from meta_neural_py import Band, Gesture, Streams

async def main():
    devices = await Band.discover(timeout=5)
    if not devices:
        raise RuntimeError("No band found; put it in pairing mode")

    # Explicitly select a discovered device in real applications.
    async with Band(devices[0].id) as band:
        async with band.subscribe(Streams(gestures=True, gyro=True)) as events:
            async for event in events:
                if isinstance(event, Gesture):
                    print(event.finger, event.action)

asyncio.run(main())
```

Additional operations:

- `Band.discover(timeout=...)`: discover without enrolling or changing ownership.
- `Band.enroll(device_id, auth=...)`: explicit interactive enrollment; persists a usable identity after confirmed completion.
- `Band(device_id, identity_store=..., reconnect=...)`: configure a connection; async context manages its lifetime.
- `band.subscribe(Streams(...), queue_size=..., overflow=...)`: acknowledged stream subscription yielding typed events. Subscription context exit requests stream shutdown; the connection context owns final disconnect.
- `band.read_hand()` / `band.set_hand("left")`: read current state or write field 10 alone and independently read it back.
- `band.device_info`, `band.battery`, `band.stats`: observable configuration, optional status, and link/queue diagnostics.
- Separate optional helpers for normalized gestures, pinch-and-turn, and EMG array conversion. NumPy should be an extra rather than a core requirement.

Define one subscription per Band instance for the first release; multiple consumers can fan out downstream. This avoids ambiguous stream ownership and one subscriber disabling another's streams. Keep callbacks out of the initial API unless a demonstrated integration needs them.

## Architecture

Keep five boundaries explicit:

1. **Transport:** scan/connect, GATT service and PSM discovery, native L2CAP byte reads/writes, disconnect, platform permissions, and local device lock. Initial backend uses PyObjC/CoreBluetooth. Bleak is useful for existing discovery/status utilities but must not be assumed to provide the required portable L2CAP transport.
2. **Protocol engine:** pure Python state machine accepting bytes and monotonic time, returning outbound bytes and typed events. Owns AirShield, DataX, request/channel correlation, deadlines, and startup ordering. No UI, filesystem paths, signal handlers, or Bluetooth objects.
3. **Identity/enrollment:** an identity-store interface, macOS Keychain implementation, BLE ownership ceremony, HTTP pairing client, and an injectable interactive login provider. Persist long-term identity separately from ephemeral transport keys.
4. **Session API:** asyncio facade, acknowledged stream lifecycle, bounded queues, cancellation, reconnect policy, and structured errors.
5. **Optional adapters:** CLI, recordings/replay, NumPy conversion, and derived interaction helpers.

Prototype the CoreBluetooth/asyncio bridge before fixing the API. Both reviewed clients use a main-thread native run loop. Test whether an integrated main-loop runner can support normal `asyncio.run`, notebooks, and GUI hosts. If that cannot be made reliable, isolate native I/O in a managed helper process with versioned local IPC; account for PyObjC execution, permissions, packaging, and shutdown. Do not promise arbitrary worker-thread operation.

Prefer a Python protocol port over bundling the Swift app or requiring an Xcode toolchain at runtime. Retain Swift fixtures as interoperability references.

## Enrollment and authenticated startup

Enrollment must have a concrete first-time-user path:

1. Discover and explicitly select a band; report whether a saved identity exists.
2. Guide Bluetooth pairing and interactive Meta sign-in. Validate the actual login callback mechanism in a Python host; Kinesis uses a WKWebView and a custom-scheme callback, so a localhost browser redirect cannot be assumed equivalent.
3. Run identity read, nonce challenge, `pair_request`, start ownership change, `pair`, and finish ownership change in the verified order. Preserve signed receipt strings exactly.
4. Store the enrolled P-256 host signing key and returned band public key in the package's own Keychain namespace; retain enough state to diagnose an interrupted ceremony without exposing secrets.
5. Reconnect and prove the saved identity works before reporting setup complete.

Normal connection order:

`BLE → L2CAP → AirShield → mutual trust → EndLinkSetup acknowledgement → device-info acknowledgement → input subscription / hand read → requested streams`

The host signs the session transcript with its persistent identity. Accept the host-result and band-proof messages in either order; verify the band proof against its stored public key before acknowledgement. Send EndLinkSetup only after both trust directions succeed. Fresh ephemeral keys, challenges, seeds, and counters are required for every connection.

Do not silently delete a rejected identity or start ownership changes on reconnect. Report an actionable identity mismatch and require an explicit enrollment operation. A legacy pairing-mode path can be retained for compatibility only after hardware validation; it is not a substitute for enrolled startup.

Optional Kinesis identity import must be user initiated and schema validated. Its Keychain service is `local.callbacked.kinesis.identity`; permission to read an app's item is not guaranteed for Python. Do not treat direct cross-app Keychain access as the default setup path or promise that phone enrollment provides an accessible Mac identity.

## Reliability, errors, and sensitive data

- Match RPC responses by channel and request ID; retain channel types correctly across untyped continuations.
- Convert `0x0300xxxx` result words into structured errors immediately. Include operation, stage, channel, and code. Distinguish `ServiceUnavailable`, `IdentityMismatch`, `TrustVerificationFailed`, `RequestTimeout`, `UnsupportedConfiguration`, `DeviceBusy`, and `StreamShutdownError`.
- Use separate deadlines for discovery, system pairing, trust, individual RPCs, first sensor data, and stop acknowledgements. Report ack-only traffic separately from sensor freshness.
- Keep native reads and crypto off slow application handlers. Bound queues; default to visible overflow failure for loss-sensitive streams, with opt-in drop policies and counters for realtime displays. Budget EMG by bytes/sample count as well as event count.
- Preserve requested stream flags during updates; verify the band's accepted flags. Start with separate gesture/motion and EMG profiles, then advertise combined operation only after sustained hardware tests. Kinesis has combined-stream update logic; old PoC runs reported lag when combined.
- On cancellation, disable active streams with a bounded acknowledgement window, disconnect, close native resources, and release the lock. Shutdown failures remain visible without hiding the original exception.
- Reconnect is bounded and opt-in initially; repeat trust and subscriptions, emit a discontinuity, and never enroll automatically. Use a per-device OS lock for package clients and clearly diagnose contention; independent apps' locks are not automatically interoperable.
- Log state transitions and counters, not signing keys, access tokens, session secrets, or signed enrollment receipts. Unlike the PoC, normal capture must not write `session_material`. Sensor recording/replay is opt-in and uses sanitized fixtures.
- Default to the stored band public key being required for verified enrolled sessions. Any compatibility exception for a missing key must be explicit and documented.

## Proposed repository layout

```text
meta_neural_py/
  pyproject.toml
  README.md
  LICENSE
  NOTICE
  src/meta_neural_py/
    __init__.py
    py.typed
    band.py
    models.py
    errors.py
    cli.py
    protocol/
      airshield.py
      datax.py
      protobuf.py
      session.py
      input.py
    transport/
      base.py
      macos.py
    identity/
      store.py
      keychain.py
      trust.py
      enrollment.py
      meta_auth.py
      meta_pair.py
    recording.py
    experimental/inference.py
  examples/
    gestures.py
    motion.py
    record_emg.py
  tests/
    fixtures/
    protocol/
    identity/
    session/
    hardware/
  docs/
    setup.md
    data-format.md
    troubleshooting.md
```

Core dependencies should be limited to `cryptography` and macOS-marked PyObjC frameworks. Decide whether Keychain and interactive login require Security/WebKit frameworks during the prototype. Use an HTTP client for enrollment; keep heavy analysis/UI dependencies optional. Build with a standard PEP 517 backend and `src` layout. Imports and offline protocol tests should work without initializing Bluetooth; unsupported platforms should fail on connection with a clear error.

## Delivery milestones and acceptance criteria

### 1. Verify prerequisites and transport integration

- Pin upstream references, check name availability, verify provenance and licensing, and choose the native/asyncio strategy.
- Discover the band and establish L2CAP from a minimal installed Python package.
- Acceptance: clean virtual environment; repeated open/close and Ctrl-C leave no orphan transport or lock. Record Bluetooth prompt behavior and supported macOS/Python versions.

### 2. Extract and test the protocol core

- Port framing, authenticated encryption, checked protobuf decoding, models, RPC correlation, and explicit errors.
- Acceptance: deterministic synthetic fixtures cover fragmented/coalesced records, MAC failure, padding, channel reuse, unsupported schema, result-code rejection, and deadline boundaries. The October 29 rejection reproduces as a service/setup error rather than a hand timeout.

### 3. Implement enrollment and trust

- Implement interactive Meta login, HTTP/BLE ceremony, Keychain persistence, and mutual-trust reconnection. Test with disposable synthetic keys first; hardware ownership changes are explicit manual integration tests.
- Acceptance: a user with neither Kinesis nor phone setup can enroll, reconnect with the saved key, and reach device info. Wrong-key, invalid-proof, duplicate-proof, reordered-proof, cancelled-login, interrupted-enrollment, and denied-Keychain-access cases fail clearly. Confirm reconnect after band restart.

### 4. Ship useful sensor access

- Add gesture, gyro, orientation, hand read/verified write, battery, and acknowledged shutdown through the public async API.
- Acceptance: examples run from another project using only the installed package; callbacks/consumers cannot stall native reads; at least a 30-minute hardware session reports packet gaps and queue drops honestly. Test left/right settings, stream cancellation, and actual disconnects.

### 5. Add EMG and recording

- Validate configuration before decoding, add batch iteration and optional NumPy conversion, plus sensor-only recording/replay with a versioned schema.
- Acceptance: decoded values and shapes agree with known fixtures and comparable hardware sessions; unsupported formats are explicit; measure sustainable throughput and loss separately for EMG-only and mixed profiles. Record both total samples and sequence discontinuities.

### 6. Publish the first standalone prerelease

- Complete setup/troubleshooting docs, type annotations, examples, changelog, license notices, and package metadata. Test built wheel and source distribution from clean environments; publish to TestPyPI first, then PyPI when explicitly authorized.
- Acceptance: install, enroll, and consume data without cloning either upstream repository, running Kinesis, installing Xcode, or extracting phone secrets. Use platform-independent protocol CI plus macOS tests; hardware tests are opt-in and never change ownership in routine CI.

### Later: experimental model data and broader platforms

Inference/model capture may temporarily modify native configuration. Snapshot, restore, independently verify restoration, and surface recovery failure; isolate this feature from normal streams. Validate new pipelines before assigning labels. Add Linux/Windows only after proving their L2CAP, permissions, identity storage, and pairing behavior. Consider a local broker only if simultaneous projects need one physical connection.

## Licensing and release boundaries

Kinesis is MIT licensed, copyright 2026 callbacked. Preserve its notice for reused/adapted source. The inspected PoC checkout contains no top-level license file; confirm permission/provenance before distributing copied PoC code. Public visibility alone does not establish reuse rights. Exclude native app binaries, proprietary firmware/models, credentials, real identity records, and secret-bearing captures from distributions and fixtures.

Enrollment/authentication uses reverse-engineered service behavior, so isolate those endpoint/schema dependencies and describe firmware/service compatibility as tested behavior. Do not claim official Meta support. A stable Python API can coexist with an explicitly experimental device backend.

## Decisions to resolve during implementation

- Which CoreBluetooth/asyncio execution strategy works in normal scripts and notebooks without requiring callers to manage Cocoa?
- Which interactive sign-in host can correctly complete Meta's actual custom callback flow in a distributed Python package?
- Which macOS versions and band firmware versions pass the complete standalone enrollment/reconnect/stream test?
- Can explicit Kinesis identity import work through permitted Keychain access, or should it remain an advanced file-import adapter?
- What sustainable combined-stream rates and queue defaults preserve acceptable latency and make loss measurable?

Recommended first build target: a transport/enrollment/trust vertical slice that prints one verified gesture through the public API. This resolves the current blocker before expanding the sensor surface.
