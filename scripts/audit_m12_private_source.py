#!/usr/bin/env python3
"""Source-only private model catalog inspection. Does NOT run tests or contact GCP."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from hosted.private_source_contract import (audit_private_model_coverage,
    DISABLED_PRIVATE_FAMILIES, RECREATED_AUTHORITY)


def main():
    try:
        catalog = audit_private_model_coverage()
    except Exception as exc:
        # Source-only helper, never expose project content or credentials.
        raise SystemExit(f'M12 private source policy FAIL: {type(exc).__name__}') from None
    print(f'M12 private source model policy PASS: {len(catalog)} registered project families')
    print('Nonportable, fail-closed:', ', '.join(sorted(DISABLED_PRIVATE_FAMILIES)))
    print('Recreated authority:', ', '.join(sorted(RECREATED_AUTHORITY)))
    print('Runtime activation: NOT PERMITTED by this source-only audit')


if __name__ == '__main__':
    main()
