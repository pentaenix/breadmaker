# Working on Breadmaker

Start with `./run` and [Contributing](../CONTRIBUTING.md). You can work on the local editor without a cloud account.

## Finding the code

- `src/api/` owns feature endpoints. The [API map](../src/api/CODEMAP.md) lists the routes and their modules.
- `src/storage/` owns SQLite storage. `src/workspace/` owns projects, packages, and backup destinations.
- `src/backend/core.py` assembles the application. Keep feature logic in its own module rather than adding it to the startup code.
- `static/js/` and `static/css/` contain browser features. Check the [frontend guide](../static/js/README.md) and [shared components](../static/css/shared/COMPONENTS.md) before adding UI.

## Local and hosted behavior

The local editor keeps projects in SQLite and files under `BREADMAKER_HOME`, which defaults to `~/.breadmaker-editor/`. Normal writing works offline after dependencies are installed. It does not need a Breadmaker account.

The hosted application uses separate accounts and storage. Authorization belongs on the server: a project ID sent by a browser is not permission to access it. See [hosted trust boundaries](HOSTED_DATA_TRUST_BOUNDARIES_M12.md) and [Security](../SECURITY.md) for the limits of the current implementation.

History, Undo, project exports, and independent backups serve different purposes. Changes to saving or recovery need tests for interrupted operations as well as successful ones.

## Checking a change

Use invented projects and credentials in tests. Run the relevant tests while working, then follow [Testing](TESTING.md) for the full release gate. Report checks that failed or were not run; a focused test pass is not a verified release.
