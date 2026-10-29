# meta-neural-py

Direct Python access to Meta Neural Band gestures, motion, and EMG.

**Experimental alpha.** Live access requires macOS and Python 3.11+. Hardware
validation is limited to one band; see [what has been tested](validation.md).
Linux and Windows support offline protocol and replay only.

Start with [installation and enrollment](getting-started.md), then follow
[Python usage](usage.md) or browse the [API guide](api.md).

Enrollment is an explicit ownership change. The default verifies the band's
proof; the tested compatibility path cannot verify it when Meta omits the key.
Read [setup and trust](setup.md) before enrolling.

Contributors can run the offline suite without a band or Meta account.
See [contributing](contributing.md) for setup and checks.
