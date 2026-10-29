# Troubleshooting

`IdentityMismatch`: no verified identity is saved, or the band rejected it.
Inspect `meta-neural status DEVICE_UUID`. Enroll explicitly if ownership should
change. Rejection does not delete keys, attempt legacy startup, reset ownership,
or reuse ephemeral keys.

`ServiceUnavailable` with code `0x0300c001`: the band rejected a service request.
The exception includes operation, stage, channel, and numeric result. Verify
setup and session trust. Increasing a hand timeout does not solve this result.

`TrustVerificationFailed`: the band proof did not verify against its saved key,
a duplicate proof arrived, or signatures were rejected. No invalid proof is
acknowledged. Check that the selected band and stored identity belong together.

`PermissionDenied`: allow Bluetooth access for the Python host in macOS System
Settings. If Bluetooth reports unsupported or powered off, check the host's
radio and execution environment. Native helper availability alone does not prove
Bluetooth access. System Bluetooth pairing and RPC trust have separate deadlines.
A reset band's old macOS Bluetooth entry can prevent pairing; forgetting that
specific old entry may be necessary before pairing again.

`DeviceBusy`: another package client owns the device OS lock. Close that client.
Independent applications have their own locks, so also close Kinesis and other
band apps if native connect fails.

`IdentityStoreError`: macOS denied a read/write, or an imported/stored record was
invalid. The package does not silently replace the Keychain with plaintext files.
Pending identity items contain recovery state and should not be discarded without
considering whether ownership changed before the failure.

`RequestTimeout`: operation/stage identify the expired deadline. After continuous
stream acknowledgement, first-sensor silence is reported separately from transport
traffic. Gesture-only silence is allowed. Use `Timeouts` to tune independent
pairing, trust, RPC, first sensor, stop, and enrollment budgets.

`QueueOverflow`: the consumer exceeded event/byte/sample storage limits. Consume
more quickly or explicitly select a dropping policy for a realtime display.
Blocking the asyncio event loop also delays protocol work, despite independent
native reads. Put CPU-heavy work in a thread/process and yield cooperatively.
Native IPC overflow fails visibly rather than growing memory without bound.

`StreamShutdownError`: the band did not acknowledge all streams disabled in the
stop window. The transport is closed. If the consumer already raised, its original
exception is preserved and gets a shutdown note. Diagnostics are also retained
in `band.stats.shutdown_errors`. Ctrl-C and task cancellation run the same cleanup.

Reconnect is opt-in, bounded per link outage, and applies to active subscriptions.
Transport disconnects can reconnect and repeat trust and subscription setup.
Identity/proof/service rejection does not cause automatic ownership changes.
A reconnect emits a discontinuity before new sensor data. If retries fail, the
iterator raises the final error.
