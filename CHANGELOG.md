# Changelog

## 0.1.0a1 — 2026-10-29

- Initial typed asyncio API, isolated CoreBluetooth helper, per-device OS lock.
- Pure AirShield/DataX/protobuf engine, strict mutual trust and RPC rejection handling.
- Explicit ownership ceremony, injectable Meta login/HTTP clients, Keychain identities.
- Gesture, gyro, orientation, supported raw EMG, verified hand write and optional battery.
- Bounded queues, opt-in reconnect, discontinuity diagnostics, sensor-only recording/replay.
- Explicit standalone missing-band-key compatibility policy, persisted separately
  from verified band-key records; available keys are always verified.
- WebKit custom-scheme callback support for fb-viewapp and oculus without the
  crashing native decision-handler block bridge.
- Bluetooth short/full UUID normalization verified against a real band.
- Live standalone enrollment/reconnect and short gesture, motion, and EMG tests
  on macOS 26.5.2 / Python 3.12.13; sustained hardware validation remains pending.
- Device packet-loss counts remain unknown until counter semantics are validated.
