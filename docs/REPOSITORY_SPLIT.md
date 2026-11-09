# Application source and hosted operations

The public [`pentaenix/breadmaker`](https://github.com/pentaenix/breadmaker) repository (`breadmaker/`) contains the Editor, local and hosted application code, tests, and application container definitions. Original Editor material is licensed under [Apache-2.0](../LICENSE); bundled assets retain their own terms in the [third-party notices](../THIRD_PARTY_NOTICES.md).

Production infrastructure, credentials, deployment pipelines, and runbooks stay in a separate private infrastructure repository. They are not included in public source releases. This separation does not replace authentication, authorization, or secret management.

Local Breadmaker remains fully usable without a Breadmaker account once its Python dependencies are installed. It stores projects in SQLite and local files. The hosted service uses separate accounts and storage.

Public source packages should contain only this repository and its public documentation. Do not include private infrastructure, credentials, project data, or combined internal release archives.
