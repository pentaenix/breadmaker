# Using Breadmaker

This guide covers the **local editor**. You can write and save without creating an account or connecting to Breadmaker Cloud. The hosted edition has a separate sign-in and storage system and must be deployed by its operator.

## 1. Open the editor

Run `./run` in the Breadmaker source directory. Breadmaker opens a browser tab connected to a server running **on your own computer**. If it doesn't open automatically, use the address printed in the terminal. Stop the server with Ctrl+C when you're done.

On first launch, Breadmaker may need to install Python dependencies. Prepare these in advance if the computer will be completely offline. After setup, ordinary writing does not need an internet connection.

Your projects are stored in `~/.breadmaker-editor/` unless you set `BREADMAKER_HOME`. Closing the browser doesn't erase the project. It is still worth verifying important edits were saved before you close the application.

## 2. Write and organize

Use **Manuscript** to work on your chapters and scenes. The other workspaces are there when you need them:

- **Canvas** — explore ideas and story structure visually.
- **Maps** — arrange and examine story connections.
- **Tasks** — track work related to your project.
- **Reviews** — prepare reading rounds and process reader feedback.

These are different views into the project, not separate documents you need to synchronize manually.

## 3. Keep earlier work

Undo helps with immediate editing changes. **History** and **checkpoints** let you revisit earlier saved states. They're useful, but they are not a substitute for a backup stored somewhere else. Restoring history is a new change in the project, not a way to delete the fact that later work happened.

## 4. Back up the project

Open **Project Settings** to find backup destinations and recovery controls. A removable drive or a separately stored verified project package gives you a copy outside your normal project directory. If a USB drive is disconnected, it cannot receive new backups; check the destination's status rather than assuming the last copy is current.

A `.breadmaker` file is a portable project package. Check that a backup verifies and can be recovered before relying on it as the only surviving copy. In this pre-alpha, older storage formats may not import into newer versions.

Google Drive is an **optional**, internet-dependent destination and needs a user-owned OAuth setup. See [Google Drive backups](GOOGLE_DRIVE_BACKUPS.md).

## 5. Share a reading round

A **Review** is a reading round with its own scope, participants, permissions, and optional questions. You can set it up in the Reviews workspace, then start it to freeze the text readers should respond to.

For an offline handoff, export a `.breadreview` file. A reviewer can open that package in Reviewer Mode, leave feedback, and return the package. You can then import the returned feedback into the matching Review. Breadmaker also supports temporarily opening a dropped review package without replacing the project you were editing.

**A `.breadreview` file is not a backup of the whole book.** It contains the material required for that review workflow, not a complete working project. If you use an optional passphrase on an exported review, share the passphrase separately from the file.

## 6. Understand local and hosted privacy

The local edition saves to files you control and doesn't require a Breadmaker account. If you connect an optional remote backup destination, that provider has its own network and credential requirements.

The hosted edition is a different deployment. It needs login and internet access, and its standard server can read stored manuscript content. Experimental private-encryption code in this repository does not mean the deployed service is end-to-end encrypted.

## Where to go next

- [README](../README.md) — installation requirements and product overview
- [Google Drive backups](GOOGLE_DRIVE_BACKUPS.md) — optional provider setup
- [Architecture](../ARCHITECTURE.md) — how files, SQLite, and hosted services fit together
- [Security](../SECURITY.md) — report a problem privately
