# Architecture and trust

For first-time installation and the shared-venv setup model, start with
[getting started](getting-started.md).

Install with Python 3.11 or newer: `python -m pip install .`. Development uses
`python -m pip install '.[dev]'`. Bluetooth connection is implemented for macOS
only. Compatibility with particular macOS, Python, and firmware versions requires
hardware testing; the current alpha has no supported hardware matrix.

Run `meta-neural discover --timeout 5`, select the intended UUID, and run
`meta-neural enroll DEVICE_UUID` explicitly. Put the intended band in pairing
mode and accept the macOS Bluetooth prompt. The login window loads Meta's
`auth.meta.com` sign-in and captures its `fb-viewapp://frl_login` or `oculus://frl_login` redirect. The
callback token must match that login attempt. The library receives tokens, never
your password, and keeps account tokens in memory only. The flow is experimental. The actual `oculus://frl_login/` callback worked
on the single host described in [validation](validation.md); other hosts remain
unvalidated.

Enrollment changes band ownership. It is never run by connection, discovery,
reconnect, tests, or identity rejection handling. There is no automatic reset.
The HTTP endpoints and client identifiers are isolated in `identity/meta_pair.py`
and `identity/meta_auth.py`; their behavior is reverse engineered and may change.

Enrollment requires BLE finish confirmation, host identity acceptance, device
info, and a successful fresh connection using the saved pending identity. By
default it also requires a valid band P-256 public key and proof verification.
The completed identity is then
committed to macOS Keychain service `org.meta-neural-py.identity.v1`. Existing
verified identities are retained until that point. Receipts are forwarded verbatim
and never logged or saved. Pending attempt stages and their keys live in a separate
Keychain item so a failed attempt does not destroy an existing saved key. Use
`meta-neural status DEVICE_UUID` to inspect setup state without Bluetooth access.
A pending item indicates an interrupted ceremony, not permission to automatically
resume or change ownership. An advanced application can inspect `load_pending()`;
this alpha deliberately does not attempt automatic receipt recovery.

A rejected identity remains saved and raises `IdentityMismatch`. Explicitly enroll
again if ownership should change. A service response without a band public key
fails verified setup; there is no implicit acceptance of an unverified band proof.
The reference app permits this exception when the service omits the key. For the
same standalone behavior, explicitly select `allow_missing_band_key=True` in
`Band.enroll`, or `--allow-missing-band-key` on the enrollment command. This creates
and stores our own fresh signing identity and uses our own login and ceremony;
it does not access Kinesis. The band's acknowledgement of our host identity is
still required. However, its own proof is unverified without its public key.
The persisted record is marked `host_verified`, rather than `verified`, and
contains the explicit compatibility policy. `band.band_verified` reports `False`
on these connections. Reconnect preserves this policy without silently enrolling
or changing it. If a band key is available, its proof is always verified, even
when compatibility mode was requested. Invalid proofs remain fatal.

If a band public key is already known from a trusted earlier setup, it can be
explicitly supplied to `Band.enroll(..., band_public_key=raw_point)` or
`meta-neural enroll DEVICE_UUID --band-public-key FILE`. The file accepts a raw
64-byte point or 65-byte uncompressed SEC1 point. The package still creates its
own fresh signing identity and verifies the band's proof against the supplied
public key. A conflicting server-returned key is rejected. This option never reads
another application's Keychain automatically and does not disable verification.

`meta-neural import-identity DEVICE_UUID RECORD.json` is an optional, explicit
file import. It verifies the raw P-256 host private/public pair and requires a valid
band public key. It does not read Kinesis's Keychain service. Keychain access denied
by macOS produces an `IdentityStoreError` without exposing item contents.

`Band` accepts an injectable `IdentityStore`, `transport_factory`, `Timeouts`,
and `ReconnectPolicy`. `Band.enroll` also accepts an injectable `MetaAuth` or a
`PairClient`. `MetaAuth` accepts a `LoginProvider` and an `HTTPClient`. These seams
support other interactive hosts and deterministic tests without native imports.
`MemoryIdentityStore` is explicitly ephemeral and mainly intended for tests.

CoreBluetooth and Foundation streams run on a managed helper process's main
Cocoa run loop. The parent uses asyncio subprocess pipes with a versioned, bounded
JSON protocol; native bytes are base64 encoded. Keys and identity logic remain in
the parent. WKWebView login has a separate UI helper. Helpers exit on parent EOF;
shutdown has bounded terminate/kill fallbacks. No arbitrary worker-thread Cocoa
execution is promised. Normal `asyncio.run` and an existing running loop do not
need to manage Cocoa; notebook and GUI-host behavior remains unvalidated.

Each selected device has an advisory OS lock in
`~/Library/Application Support/meta-neural-py/locks`. Files remain after release
to avoid inode races. A crash releases the OS lock automatically. Another app's
independent lock cannot be detected; close Kinesis or other band clients before
connecting directly.
