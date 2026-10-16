# Public Editor and private Cloud repositories

Breadmaker keeps application source and hosted operations in separate repositories:

- **[`pentaenix/breadmaker`](https://github.com/pentaenix/breadmaker)** (`breadmaker/`) contains the Editor, local and hosted application code, tests, and application container definitions. This is the repository intended for public viewing. Original Editor material is licensed under [Apache-2.0](../LICENSE); bundled third-party assets retain their own terms (see [notices](../THIRD_PARTY_NOTICES.md)).
- **`pentaenix/breadmaker-cloud`** (`breadmaker-cloud/`) is private and proprietary (see its own `LICENSE`). It owns OpenTofu infrastructure, GCP configuration, deployment pipelines, operational monitoring, and runbooks.

The separation keeps production access and environment details out of the public application repo. It is not a substitute for authentication, authorization, or secret management. Application security must not depend on the Cloud repository being hidden.

Local Breadmaker remains fully usable without a Breadmaker account once its Python dependencies are installed. The **local Editor** never requires a hosted account or GCP for writing once its dependencies are installed. It uses SQLite and local files. The **hosted service** uses a separate account and storage system.

The maintainer may distribute a *private combined release ZIP* containing both repository roots. **Do not publish that combined archive.** Public source packages must contain only the Editor repository and public documentation. Infra changes must be made through OpenTofu in the private Cloud repository.
