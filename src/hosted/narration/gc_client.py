"""Daily invocation job: metadata identity + HTTPS ONLY, no data-plane clients.

The job's service account needs no Firestore, Storage or KMS roles. Configuration
does not select buckets, projects, object names, cursors or deletion payloads.
Ambiguous failures are NOT retried; server cursors/leases recover on the next run.
"""
import json
import os
import re
import time

from hosted.narration.maintenance_identity import PATH, origin
from hosted.narration.pronunciation_client import METADATA_IDENTITY, _session

MAX_BATCHES = 8
FIELDS = {'ok', 'collected', 'held', 'tracked', 'fences', 'orphans', 'partial'}


def _read(response, maximum, deadline):
    chunks, size = [], 0
    for part in response.iter_content(chunk_size=1):
        size += len(part)
        if size > maximum or time.monotonic() >= deadline:
            raise ValueError('NARRATION_GC_RESPONSE_INVALID')
        chunks.append(part)
    return b''.join(chunks)


def invoke(public_origin, *, session_factory=_session):
    url = origin(public_origin) + PATH
    totals = dict(collected=0, held=0, tracked=0, fences=0, orphans=0, partial=True, batches=0)
    with session_factory() as session:
        for _ in range(MAX_BATCHES):
            until = time.monotonic() + 55
            with session.get(METADATA_IDENTITY, params={'audience': url, 'format': 'full'},
                    headers={'Metadata-Flavor': 'Google'}, timeout=(1, 3), allow_redirects=False, stream=True) as response:
                if response.status_code != 200 or response.headers.get('Metadata-Flavor') != 'Google':
                    raise ValueError('NARRATION_GC_IDENTITY_UNAVAILABLE')
                token = _read(response, 8192, until).decode('ascii')
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
                    raise ValueError('NARRATION_GC_IDENTITY_UNAVAILABLE')
            with session.post(url, data=b'', headers={'Authorization': 'Bearer ' + token},
                    timeout=(2, 50), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ValueError('NARRATION_GC_FAILED_RESUMABLE')
                result = json.loads(_read(response, 8192, until))
                if (not isinstance(result, dict) or set(result) != FIELDS
                        or type(result['ok']) is not bool or type(result['partial']) is not bool
                        or any(type(result[key]) is not int or result[key] < 0 for key in FIELDS - {'ok', 'partial'})
                        or result['ok'] != (result['orphans'] == 0)):
                    raise ValueError('NARRATION_GC_RESPONSE_INVALID')
                totals['batches'] += 1
                for key in FIELDS - {'ok', 'partial'}:
                    totals[key] += result[key]
                totals['partial'] = result['partial']
                if not result['ok'] or not result['partial']:
                    break
    return {'ok': totals['orphans'] == 0 and not totals['partial'], **totals}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', required=True, choices=['COLLECT_DERIVED_NARRATION_ONLY'])
    parser.parse_args()
    if (os.environ.get('BREADMAKER_OPERATOR_MODE') != 'narration_gc'
            or os.environ.get('BREADMAKER_PUBLIC_ORIGIN') != 'https://app.breadmaker.studio'):
        raise SystemExit('NARRATION_GC_CONFIGURATION_REQUIRED')
    try:
        result = invoke(os.environ['BREADMAKER_PUBLIC_ORIGIN'])
    except Exception:
        print(json.dumps({'ok': False, 'code': 'NARRATION_GC_FAILED_RESUMABLE'}))
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))
    if not result['ok']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
