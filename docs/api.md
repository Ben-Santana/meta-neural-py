# Public API guide

Import the main types from `meta_neural_py`. This alpha API may change before a
stable release. Methods that perform I/O below are asynchronous unless noted.

## Discovery, enrollment, and connection

- `await Band.discover(timeout=5)` returns `list[Device]` with `id`, `name`, and
  optional `rssi`. Select the intended UUID explicitly.
- `await Band.enroll(device_id, *, allow_missing_band_key=False, band_public_key=None)`
  changes ownership and returns the saved `Identity` after a fresh connection.
  A trusted raw band public point may be supplied explicitly. See [trust](setup.md).
- `Band(device_id, *, reconnect=None, timeouts=None, identity_store=None)` is an
  async context manager. Entry loads an existing identity and connects; it never
  enrolls. `ReconnectPolicy(attempts=0, delay=1.0)` disables retries by default.
- `Timeouts` configures discovery, pairing, trust, RPC, first-sensor, stop, and
  enrollment deadlines in seconds. Defaults are 5, 60, 10, 8, 15, 3, and 180.

Advanced injection seams include `transport_factory` and `lock_directory` on
`Band`; enrollment additionally accepts `auth` and `pair_client`.
`MetaAuth` lives in `meta_neural_py.identity.meta_auth` and accepts injectable
login and HTTP providers. The default `KeychainIdentityStore` lives in
`meta_neural_py.identity.keychain`; `IdentityStore`, `Identity`, and the ephemeral
`MemoryIdentityStore` are exported at package level.

## Subscriptions and events

`band.subscribe(streams, *, queue_size=256, overflow="error",
queue_bytes=1048576, queue_samples=4096)` returns a `Subscription` async context
manager and async iterator of `Event`. Both entry and exit await accepted stream
flags. Subscribe inside an active `Band` context; one subscription is permitted.

`Streams(gestures=True, gyro=False, orientation=False, emg=False)` selects sensors.
At least one stream must be enabled. Overflow policies are `"error"`,
`"drop_oldest"`, and `"drop_newest"`.

- `Gesture`: `finger`, `action`, and `derived_action` names plus preserved numeric
  codes and `synthetic`.
- `Gyroscope`: three integer `values`; `Orientation`: four float `values` in
  `w,x,y,z` order. Physical units/conventions remain unvalidated.
- `EMGBatch`: `values`, `payload`, and optional `as_numpy()` conversion.
- `Discontinuity`: reconnect/counter information; `ConnectionState`: connection
  state changes. Do not assume every event is sensor data.

`SensorEvent` includes `sequence` and device `timestamp`; `Event` includes
`received_at` and `source_clock`. See [data format](data-format.md) for clock domains.

## Configuration and diagnostics

- `await band.read_hand()` and `await band.set_hand("left" or "right")` return
  the current hand. Writes are independently read back.
- `await band.read_battery()` returns `Battery` or `None` if unavailable.
- `band.device_info`, `band.emg_configuration`, `band.hand`, and `band.battery`
  expose available connection information; optional values may be unset.
- `band.band_verified` reports band proof verification for the connection.
- `band.stats` exposes traffic, sensor counts, local drops, discontinuities,
  reconnects, and shutdown diagnostics. Device packet loss is unknown:
  `sequence_gaps` remains `None`.

Errors are defined in `meta_neural_py.errors`. Start with
[troubleshooting](troubleshooting.md) for recovery guidance. Connection errors do
not grant permission to reset ownership or discard identities.

## Record and replay

From `meta_neural_py.recording`, use `await record(events, path)` for a new
sensor-only JSONL file, `read_event(line)` to decode a record, and
`replay(path, speed=0)` as an async iterator. See [data format](data-format.md)
for supported records and timing behavior.
