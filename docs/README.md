# Documentation

New to Breadmaker? Start with the [README](../README.md), then [Using Breadmaker](USING_BREADMAKER.md). These explain the local application without requiring you to understand the hosting architecture.

## For contributors

| Topic | Guide |
| --- | --- |
| How the runtimes fit together | [Architecture](../ARCHITECTURE.md) |
| How to run the tests | [Testing](TESTING.md) |
| Source entry points | [API map](../src/api/CODEMAP.md) and [code index](CODE_INDEX.json) |
| Project files and exports | [Project package format](BREADMAKER_PROJECT_JSON.md) |
| Reviews and portable feedback | [Review implementation plan](BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md) |
| Optional Google Drive backups | [Google Drive backups](GOOGLE_DRIVE_BACKUPS.md) |
| Hosted security and limits | [Hosted trust boundaries](HOSTED_DATA_TRUST_BOUNDARIES_M12.md) and [M12 source closure](security/M12_SOURCE_CLOSURE.md) |

## License and reuse

The public Editor is [Apache-2.0 licensed](../LICENSE). See [NOTICE](../NOTICE)
and [third-party notices](../THIRD_PARTY_NOTICES.md) for attribution.
Deployment controls live in the separate proprietary `pentaenix/breadmaker-cloud`
repository and are not covered by the Editor license.

## Older material

The [archive](archive/), older milestone files, and the collapsed historical sections of the root guides explain how the design evolved. They are useful when investigating a regression; they are **not installation instructions**. Older references to Auth0, dedicated staging, or experimental deployments should not be followed as current operating procedure.

The live infrastructure and OpenTofu runbook are maintained separately in the private `breadmaker-cloud` repository. This public repository cannot be deployed simply by following one of the historical milestone files.
