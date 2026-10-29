# Publish the first alpha

The intended initial version is `0.1.0a1`, distribution `meta-neural-py`. Publication
follows the user's band tests. Do not mark hardware validation as passed based on
synthetic protocol tests, a successful build, or read-only discovery alone.

Install release tooling and validate:

```sh
python -m pip install '.[dev,release]'
pytest
ruff check src tests
mypy src/meta_neural_py
python -m build
python -m twine check --strict dist/meta_neural_py-0.1.0a1*
```

Both distributions must install from outside the source tree in fresh virtual
environments. Check that README compatibility claims and validation.md describe
actual results. Confirm the intended package name is still available; an earlier
404 is not a reservation. Review the wheel and source file list for private data.

Use the PyPI account's API token through Twine's password prompt or an approved
credential store. Username is `__token__`. Do not paste the token in chat, place it
in a command argument, commit it, or save it in a release report. Configure TestPyPI
separately; its accounts and tokens are independent from production PyPI.

First upload to TestPyPI, then verify installation. When installing an alpha from
TestPyPI, use its index for the package and obtain dependencies separately from
PyPI rather than implicitly trusting duplicate names across indexes:

```sh
python -m twine upload --repository testpypi dist/meta_neural_py-0.1.0a1*
```

After the tested artifacts and account are ready, upload that same reviewed version
to production as authorized by the user:

```sh
python -m twine upload dist/meta_neural_py-0.1.0a1*
```

Confirm the project page and install `meta-neural-py==0.1.0a1` in a fresh environment.
Run its CLI and protocol smoke checks. Uploaded release filenames cannot be reused;
fixes need a new version. Do not rebuild and upload changed content under a version
whose artifacts have already been published.

Official references:
- https://packaging.python.org/en/latest/tutorials/packaging-projects/
- https://docs.pypi.org/api/upload/

## GitHub repository and documentation

The source repository is [Ben-Santana/meta-neural-py](https://github.com/Ben-Santana/meta-neural-py).
Documentation lives in `docs/` and is browsable through the
[documentation index](index.md). Submit documentation changes in pull requests
alongside code changes. Keep setup and API guidance versioned with the library.
No hosted site or Wiki is required.

Before pushing, inspect `git status --short` and the staged file list. Exclude
virtual environments, caches, build outputs, account data, identities, receipts,
and private recordings. Keep LICENSE and NOTICE. The source distribution uses an
explicit include list; inspect wheel and source archive contents too.

Package metadata links to the repository, documentation index, and issues.
CI checks offline tests, lint, formatting, types, and packaging. A push does not
publish a PyPI release.
