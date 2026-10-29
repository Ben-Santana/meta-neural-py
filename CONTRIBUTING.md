# Contributing

Bug reports, documentation, offline protocol tests, and small focused fixes are
welcome. The package is an experimental alpha; distinguish observed hardware
behavior from assumptions. No band or Meta account is needed for offline work.

## Development setup

Clone the repository, enter its directory, then use Python 3.11+:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,docs,release]'
pre-commit install
```

## Required checks

```sh
ruff format src tests examples
ruff check src tests examples
ruff format --check src tests examples
mypy src/meta_neural_py
pytest
mkdocs build --strict
python -m build --outdir /tmp/meta-neural-dist
python -m twine check --strict /tmp/meta-neural-dist/*
```

Use a fresh output directory for each package validation. CI runs offline tests
on Linux/macOS with Python 3.11–3.13, plus lint, types, docs, and packaging checks.
Pre-commit runs Ruff lint and formatting; it may modify files, so review and stage
those changes before committing. Preview documentation with `mkdocs serve`.

## Pull requests and reports

Keep PRs focused. Explain the problem, resulting behavior, and checks run. Update
docs/examples for public behavior changes. Add regression coverage for meaningful
logic fixes. Use the issue forms for reproducible bugs and proposed features;
include Python/macOS/package versions and sanitized error details.

Never upload account tokens, passwords, signing keys, Keychain exports, ownership
receipts, or private recordings. Reproduce protocol bugs with synthetic fixtures.
Sensor-only recordings can still contain private information; do not attach them
by default. Do not paste raw authentication/network dumps into issues.

## Hardware work

Ordinary tests and CI must not open login windows, use accounts, connect to a band,
or change ownership. Follow [hardware testing](docs/hardware-testing.md) for opt-in
checks. Enrollment is an explicit ownership change; do not make it an automatic
recovery path. Report the host/firmware, duration, stream profile, drops, and proof
verification policy when describing a hardware result.

Preserve the MIT license and reference provenance in NOTICE. See
[publishing](docs/publishing.md) for release and GitHub Pages setup; a passing PR
does not publish a package.
