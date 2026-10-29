# Documentation

Experimental direct Python access to Meta Neural Band gestures, motion, and EMG.
Live Bluetooth requires macOS and Python 3.11+. Linux and Windows support offline
protocol and replay only. See [validation](validation.md) for tested hardware and
outstanding limitations.

## Start here

- [Getting started](getting-started.md): install, enroll, and reuse setup across venvs.
- [Python usage](usage.md): gestures, motion, EMG, recording, and reconnect.
- [Public API](api.md): connection, subscriptions, configuration, and diagnostics.
- [Troubleshooting](troubleshooting.md): permissions, trust, timeouts, and queues.

## Details and development

- [Architecture and trust](setup.md): explicit ownership and verification policies.
- [Data format and replay](data-format.md): sensor units, clocks, and recordings.
- [Validation and limitations](validation.md): observed hardware behavior and gaps.
- [Hardware testing](hardware-testing.md): opt-in testing without accidental enrollment.
- [Contributing](../CONTRIBUTING.md): development setup, checks, and pull requests.
- [Publishing](publishing.md): package validation and release workflow.

These guides live with the source so documentation changes can be reviewed with
code changes. Use GitHub's file search to find topics. Start at the
[project README](../README.md) for the quick introduction.
