# RELEASING.md

How to cut a Workproof release. Follow these steps in order; do not skip the
verification steps.

## 1. Pre-release checks

```bash
# All tests must pass
python -m pytest -q

# Ruff must be clean
ruff check src tests
ruff format --check src tests

# Line count must be under 3000
find src -name '*.py' -exec wc -l {} + | tail -1

# Acceptance test must pass
bash scripts/acceptance_test.sh
```

If any of these fail, **stop**. Do not tag a release with failing tests.

## 2. Bump the version

Edit `pyproject.toml`:

```toml
version = "0.1.0"  →  "0.2.0"
```

Update `DECISIONS.md` with a new entry documenting the version bump and any
schema changes.

## 3. Update the Action version input default

In `action.yml`, update the `version` input default to match the new tag:

```yaml
  version:
    default: "v0.2.0"
```

## 4. Commit and tag

```bash
git add -A
git commit -m "release: v0.2.0"
git tag -a v0.2.0 -m "Workproof v0.2.0 — evidence freshness check"
git push origin main
git push origin v0.2.0
```

The tag is what the Action's `version` input pins to. Users who set
`version: v0.2.0` in their workflow will install exactly this tag.

## 5. Publish to PyPI (when ready)

Workproof is not yet on PyPI. When it is:

```bash
python -m build
python -m twine upload dist/*
```

Then update `action.yml` to install from PyPI instead of git:

```yaml
run: |
  python3 -m pip install --upgrade pip
  python3 -m pip install "workproof-cli==${WORKPROOF_VERSION#v}"
```

The package name on PyPI is `workproof-cli` (the CLI command remains `workproof`).

## 6. Verify the release

Open a test PR against a repo with the Workproof Action installed. Confirm:
- The Action installs from the pinned tag without error.
- The sticky comment is posted with `VERIFIED`.
- The commit status shows `Workproof: success`.

## 7. Announce

- Update the GitHub Release notes with the changelog.
- Post to the project's communication channels (if any).

## Schema versioning rules

- Receipt schema versions (`schema_version` field) are independent of package
  versions. Schema `0.1` and `0.2` can ship in the same package.
- **Never break verification of old schema versions.** A v0.2 verifier must
  still accept v0.1 receipts. The `parse_receipt` function gates on
  `SUPPORTED_SCHEMA_VERSIONS`; adding a version is additive.
- New checks (like `evidence_freshness` in 0.2) apply only to receipts whose
  `predicateType` matches the new schema URI. Old receipts skip the check
  with a visible `skip` status so the reviewer knows it wasn't evaluated.
