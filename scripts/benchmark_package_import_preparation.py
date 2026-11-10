#!/usr/bin/env python3
"""Time provider-free package validation/planning; never import or publish it.

Use a synthetic or explicitly authorized local archive. Output is aggregate
timing/counts only: no project title, identities, content, or credentials.
"""
from __future__ import annotations

import argparse
import cProfile
import json
from pathlib import Path
import pstats
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--profile', action='store_true', help='Print the top 25 cumulative planner functions')
    args = parser.parse_args()
    from hosted.native_package import prepare_project_package
    from hosted.package_import import MAX_SOURCE_BYTES
    started = time.perf_counter()
    with args.package.open('rb') as source:
        raw = source.read(MAX_SOURCE_BYTES + 1)
    read_seconds = time.perf_counter() - started
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('HOSTED_PACKAGE_TOO_LARGE')
    profile = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    if profile is not None:
        profile.enable()
    try:
        plan = prepare_project_package(raw, 'f' * 32)
    finally:
        if profile is not None:
            profile.disable()
    elapsed = time.perf_counter() - started
    print(json.dumps({'source_bytes': len(raw), 'read_seconds': round(read_seconds, 6),
        'planner_seconds': round(elapsed, 6), 'records': len(plan['records']),
        'annotations': len(plan['annotations']), 'assets': len(plan['assets']),
        'profile_enabled': args.profile, 'provider_operations': 0}, indent=2))
    if profile is not None:
        pstats.Stats(profile).strip_dirs().sort_stats('cumulative').print_stats(25)


if __name__ == '__main__':
    main()
