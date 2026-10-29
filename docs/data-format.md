# Data and diagnostics

Every emitted event has `received_at`, a host monotonic time in seconds, and
`source_clock`, an explicit clock-domain label. Sensor events also have integer
`sequence` and `timestamp`. The gesture, motion, and EMG device clocks are kept
separate. `received_at` records the parent's receipt/decoding time and can include
IPC scheduling delay. No wall-clock or anatomical calibration is implied.

`Gesture` exposes numeric `finger_code`, `action_code`, and `derived_action_code`,
plus `synthetic`. Its `finger`, `action`, and `derived_action` properties provide
labels; unknown values become `unknown:NUMBER` while the original code remains.
Raw and derived events are not filtered or deduplicated by the core API.

`Gyroscope.values` is a tuple of three signed little-endian int16 wire counts.
No angular velocity scale or anatomical axis mapping is asserted.
`Orientation.values` contains four finite float32 values in observed reference
order `w,x,y,z`. Coordinate conventions remain unvalidated.

`EMGBatch.values` contains 16 rows of 8 unsigned ADC counts. Batches must match
2048 Hz / 8 channels / 16-bit / 16 samples / encoding 0. Configuration is read
and checked before enabling the stream. Unsupported layouts raise
`UnsupportedConfiguration`. Each batch retains its original sensor protobuf
`payload`. `band.emg_configuration` exposes the validated current layout.
Optional `as_numpy()` returns a `(16, 8)` uint16 array. There are no
voltage, electrode-location, or finger-joint interpretations. An EMG-only profile passed a short hardware smoke test; sustained and mixed
throughput remain unvalidated. See [validation](validation.md).

`band.device_info` retains checked raw protobuf fields. Its `firmware` and `model`
labels are `None` until their schema is validated. `band.battery` is either `None`
or a `Battery(level, charging)`; extended battery rejection cannot kill streams.
`band.hand` is updated only after an independent configuration read. `band.state`
and `band.active_streams` report connection state and acknowledged configuration.

`band.stats` separates traffic, authenticated records, sensor events, ADC samples,
EMG payload bytes, queue drops, and sequence discontinuities. `last_traffic_at`
counts any received bytes; `last_sensor_at` changes only for decoded sensor data.
An acknowledged subscription is not evidence that sensor data is fresh. The first
continuous-sensor deadline applies when gyro, orientation, or EMG is requested;
a gesture-only session may stay silent until the user makes a gesture.

Sequence continuity is tracked separately for each sensor event type. An
increasing but nonconsecutive sequence is reported as an unvalidated step.
Reset, wrap, duplicate, backward timestamps, or reordered frames also emit
`Discontinuity(missing=None)`. Live testing showed that gesture counters cannot
be interpreted as a single consecutive packet counter. Counter semantics across
raw and derived sources remain unvalidated, so `sequence_gaps` is `None` and no
exact device packet-loss count is claimed. New subscriptions and reconnects reset continuity;
reconnects explicitly emit a discontinuity. Queue drops are local consumer loss
and never included in device sequence gaps.

One subscription owns the stream flags. Queues enforce event count, approximate
payload bytes, and EMG sample count. Default overflow is `QueueOverflow` on the
next iteration. Opt-in `drop_oldest` and `drop_newest` increment local drop
counters. Byte budgets count sensor payload size for EMG and a fixed budget for
other events; they do not claim to measure Python heap allocation.

`recording.record(events, path)` creates a new sensor-only JSONL file, refusing
overwrite. Schema is `meta-neural-py.sensor.v1`. Only sensor/discontinuity classes
are accepted; encrypted frames, identity records, tokens, receipts, and session
keys cannot be recorded through this API. `replay(path, speed=0)` validates the
schema and yields immediately; positive `speed` scales recorded receive intervals.
Original receive/device timestamps remain intact and belong to their original
clock domains. Recording is opt-in; there are no default captures.

Experimental inference and model switching remain deferred. No handwriting
recognition or fixed nine-class pipeline is exposed.
