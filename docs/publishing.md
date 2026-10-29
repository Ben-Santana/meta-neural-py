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

## GitHub repository and Pages

Before the first push, inspect `git status --short` and the staged file list.
Exclude virtual environments, caches, build outputs, account data, identities,
receipts, and private recordings. Keep LICENSE and NOTICE. The source distribution
uses an explicit include list; inspect wheel and source archive contents too.

Once the GitHub owner/repository is chosen, set `[project.urls]` in
`pyproject.toml` for Repository, Documentation, and Issues. Set `repo_url` and
`site_url` in `mkdocs.yml` to the repository and its actual Pages URL. Do not
publish placeholder links. Initialize the local repository on `main`, prepare the
initial commit, and push to the intended public GitHub repository.

In repository Settings → Pages, select **GitHub Actions** as the source. The
`documentation` workflow checks every PR and deploys only the default branch.
No PyPI upload occurs. Preview locally with `mkdocs serve`; validate with
`mkdocs build --strict`. Relative URLs support project Pages subpaths.

The site uses MkDocs Material with navigation and search. The site contribution
page is generated from the root CONTRIBUTING.md by `docs/hooks.py`; edit the root
guide. Documentation dependencies are isolated in the `docs` extra.
