# Test a band before publishing

Close Kinesis and other band clients, charge the band, keep it nearby, and put it
in its documented discoverable/pairing mode. Discovery is read-only:

```sh
.venv/bin/meta-neural discover --timeout 8
```

On hosts that restrict Bluetooth in a sandbox, discovery must run from an authorized
normal host process. A sandbox's “unsupported” state does not establish that the
Mac lacks Bluetooth. Select the UUID printed for the intended band.

Inspect this package's saved identity state without connecting:

```sh
.venv/bin/meta-neural status DEVICE_UUID
```

If there is no saved identity, choose setup explicitly. First-time enrollment
changes ownership and opens Meta sign-in:

```sh
.venv/bin/meta-neural enroll DEVICE_UUID
```

The current Meta service may omit the band verification public key. For standalone
setup using the same missing-key behavior as Kinesis, explicitly choose
`enroll DEVICE_UUID --allow-missing-band-key`. This persists an unverified-band-proof
policy; the package still provisions and confirms its own new host identity.
The test command prints `Band proof verified: False` for those sessions. Do not
report them as mutually verified connections. Default enrollment remains strict.

If the band is already enrolled to Kinesis, a normal connection cannot use its
identity automatically. Either explicitly enroll it for this package, or use the
optional validated identity-file import described in setup.md. Do not factory reset
the band or extract another app's secrets as a routine test step. An existing phone
or Kinesis enrollment is not proof that this package has a usable signing key.

Once a verified identity is saved, run:

```sh
.venv/bin/meta-neural test DEVICE_UUID --duration 60 --profile gestures
.venv/bin/meta-neural test DEVICE_UUID --duration 60 --profile motion
.venv/bin/meta-neural test DEVICE_UUID --duration 60 --profile emg
```

Make several index/middle finger gestures during the gesture test. Each test must
reach authenticated device info, read the current hand, acknowledge its requested
streams, receive sensor events, acknowledge shutdown, and close the transport.
Battery is optional. The test prints event counts and link/queue diagnostics;
it does not write a recording, alter handedness, or enroll automatically. No
sensor events is a failed check, even if all RPCs were acknowledged.

Repeat connection/close, then interrupt a running test with Ctrl-C and check that
the next connection can acquire the lock. Perform an explicit band restart and
verify that the saved identity reconnects. Follow with a sustained session, e.g.
`--duration 1800`, while monitoring queue drops and sequence discontinuities.
Mixed motion and EMG rates and physical coordinate conventions need their own
validation before any compatibility claims.

Document tested macOS/Python/firmware, test durations, observed event types,
inferred sequence gaps, local drops, and shutdown outcomes in validation.md.
Do not commit sensor recordings, account tokens, signed receipts, identity files,
or Keychain exports. Hardware tests and ownership changes remain manual.
