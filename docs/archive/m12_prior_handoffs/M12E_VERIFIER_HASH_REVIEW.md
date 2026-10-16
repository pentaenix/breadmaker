# M12E — protected verifier hash review (2026-10-14)

## Outcome and operator evidence

- **Editor 10.5.13 (owner executed)**: Unit 89 pass; Core/API 1,333 pass, 3 fail, 1 skip; current browser 94 pass; durable E2E 92 pass; total 1,608 pass, 3 fail, 1 skip. No timeouts. The 3 failures have the SAME protected SHA mismatch for `scripts/verify_release.py` (`d2f145f8...` actual vs `c5de57ae...` stale expected).
- **Cloud 10.4.11**: previous owner verification 133/133 green; identical Cloud bytes in the corrective.
- **New candidate**: Editor 10.5.14 / Cloud 10.4.11, pending owner Editor verification.

## Audited difference and fix

Inspected `scripts/verify_release.py` from bundled releases 10.5.11, 10.5.12, and 10.5.13. The 10.5.11->10.5.12 diff was exactly these entries in `PREWARM_APP_CORE_MODULES`, plus explanatory comments:

```python
    "test_v10326.py",
    "test_v700.py",
```

10.5.12 and 10.5.13 are **byte-identical** for this script. This change shifts first app initialization into pre-test warmup while preserving the `tests/conftest.py` test budgets and watchdog behavior. No other protected paths changed during the reviewed verifier adjustment.

| Source | Approved SHA-256 |
|---|---|
| `scripts/verify_release.py` in 10.5.11 | `c5de57ae7b0a781cde65f4abca6cc620bf6a0d774f937a182cfbf1730187c837` |
| `scripts/verify_release.py` in 10.5.12–10.5.14 | `d2f145f8c1f2e4c8559eba060e80f91046f766bbc596dd8a90a8b6f55cf9c6ed` |

**Corrective:** replace exactly that value in `tests/test_v10353.py`, `tests/test_v10354.py`, `tests/test_v10360.py`. Keep all other protected-file hashes, the tests, the verifier, the per-test 10s Core/15s browser watchdogs and test reporting untouched. Bump Editor patch version and synchronize only existing release metadata. Do not replace strict digests with broad assertions or disable the firewall.

## Safety and successor-agent instructions

- **NEVER run** `./verify-release`, `./test`, pytest, Playwright or any test-suite wrapper as an agent. User runs the full Editor verifier and reports failure digest. Agent may inspect sources, compare hashes, parse Python/JSON/JS/shell syntax, check release-metadata consistency, and verify ZIP CRC.
- Editor 10.5.14 remains a *candidate*, not a verified release, until user returns green. Do not mark historic 10.5.12/10.5.13 candidates as green.
- Preserve all security controls, tenant authorization, IAM, local persistence and KMS separation. Experimental Firestore Task and milestone encryption remains **OFF in normal hosted runtime**, with many user-data families, packages and recovery still NOT_INTEGRATED. No GCP deployment yet.
- **Local Breadmaker always works standalone** with no account, network, GCP, KMS or telemetry. Do not introduce new local dependencies.
- Next engineering work upon green: per-model private Firestore content classification, encrypted Reviews/Canvas/chapter metadata and encrypted archive/recovery lifecycles, bounded queries, distributed rate limits and genuine live IAM verification in operator-controlled M13.
