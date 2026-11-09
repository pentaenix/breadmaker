# Contributing to Breadmaker

Thanks for helping with Breadmaker. You can work on most features locally without a cloud account. For a larger change, please open an issue first so we can discuss it before you spend time on it.

## Get started

1. Read the [README](README.md), then use `./run` to start the local editor.
2. Follow the source ownership map in the [development guide](docs/DEVELOPMENT.md). API routes are indexed in [`src/api/CODEMAP.md`](src/api/CODEMAP.md); frontend features are described in [`static/js/README.md`](static/js/README.md).
3. Reproduce the change or bug with a **synthetic project**. Never share an unpublished manuscript or real login credentials in an issue or test fixture.
4. Make a focused change with tests that exercise behavior, not just filenames or implementation details.
5. Run the relevant test locally, then the full release gate before opening a release-ready pull request:

```sh
./verify-release
```

The full suite covers unit, Core/API, browser, and durable end-to-end behavior. It installs additional tools and Chromium as needed, and the dependency audit may require internet access. See [Testing](docs/TESTING.md).

## Working on the source

- Backend feature routes belong in `src/api/`; shared storage logic belongs with its owning storage or workspace module.
- The local application must work without accounts, GCP, KMS, or hosted APIs after dependencies are installed.
- Hosted requests must obtain user and project authority on the server, not from browser-supplied IDs.
- History, backups, and Recovery protect different failure modes. Preserve their durability and error-handling guarantees.
- Pre-alpha data formats may change without migration. Avoid compatibility code for unsupported storage formats unless it is explicitly required.

If you change the API/module layout, regenerate the checked-in navigation files:

```sh
python scripts/generate_api_map.py
python scripts/generate_code_index.py
```

Do not commit `.venv/`, local project data, private exports, API keys, OAuth tokens, or generated reports. See [SECURITY.md](SECURITY.md) before reporting a security issue.

## Changes that affect deployment

The public Editor repository runs its full verification gate on pull requests and protected-main pushes. The maintainer's private Cloud pipeline must also pass before deployment. Ordinary forks don't need Cloud credentials; if private dispatch is configured, missing or invalid credentials are a failure, not permission to skip the deployment gate.

GCP resources and production releases are managed by OpenTofu in a **separate private infrastructure repository**. Do not add direct cloud mutations to the Editor repository or assume this repository can deploy production on its own.

## License

The public Editor is licensed under [Apache-2.0](LICENSE). By submitting a
contribution, you agree that your contribution is provided under this license
unless you clearly state otherwise and the maintainers agree. Submit only
material you have the right to contribute. Record attribution and any
third-party licenses in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Contributing to the Editor does not grant access to hosted deployments.

## Architecture and component rules

Feature modules must not import `backend.core`; use the canonical domain owner.
`src/backend/core.py` is composition-only. `static/app.js` is a startup shim only.
The frontend architecture freeze preserves the ordered classic-script model and
stylesheet cascade. Follow reuse-before-create: inspect
`static/css/shared/COMPONENTS.md`, reuse existing components and use the
`tool-icon-button` primitive for matching toolbar controls.

Keep local editing accountless and offline after installation. Browser IDs are
not authorization. Never commit credentials, manuscripts or generated backups.
Review protected-file changes explicitly; preserve release watchdogs and checks.
