"""Small account-day records. No text, names, browser clocks or client counts."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time

from hosted.usage_consent import FIELD, NOTICE_VERSION, NOTICE_SHA256

SOURCE = 'hosted_activity_v1'
DAYS = 'activity_days_v1'
TASKS = 'activity_task_versions_v1'
RETENTION_DAYS = 28  # Leave cleanup headroom inside the notice's 30-day maximum.
MAX_TASKS = 256
STATES = ('open', 'later', 'done', 'archived')
COUNTERS = ('manuscript_commits', 'projects_created', 'chapters_created',
            'scenes_created', 'tasks_created', 'canvases_created', 'cards_created',
            'maps_created', 'map_lines_created', 'stations_created',
            'image_assets_registered', 'reviews_created', 'tasks_completed', 'tasks_archived')
COLLECTIONS = {'chapters': 'chapters_created', 'scenes': 'scenes_created',
               'tasks': 'tasks_created', 'canvases': 'canvases_created',
               'nodes': 'cards_created', 'story_maps': 'maps_created',
               'lines': 'map_lines_created', 'items': 'stations_created',
               'assets': 'image_assets_registered', 'reviews': 'reviews_created'}


def enabled():
    flag = os.environ.get('BREADMAKER_ACTIVITY_ENABLED', '')
    if flag not in ('', 'false', 'true'):
        raise RuntimeError('ACTIVITY_CONFIGURATION_INVALID')
    if flag != 'true':
        return False
    started = os.environ.get('BREADMAKER_ACTIVITY_STARTED_AT', '')
    if (not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', started)
            or not 0 < datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp() <= time.time()
            or os.environ.get('BREADMAKER_ACTIVITY_PRODUCER_SHA256') != bundle_hash()):
        raise RuntimeError('ACTIVITY_CONFIGURATION_INVALID')
    return True


def bundle_hash():
    root = Path(__file__).resolve().parents[2]
    paths = ['hosted/activity/' + name + '.py' for name in ('records', 'journal', 'reader', 'http_api')]
    value = hashlib.sha256()
    for name in sorted(paths):
        value.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return value.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     default=str).encode()).hexdigest()


def eligible(row):
    if row is None:
        return None
    data = dict(row.data)
    choice = data.get(FIELD, {})
    if (data.get('status') != 'active' or data.get('_bm_erasure_operation')
            or type(data.get('created_at')) is not str or not isinstance(choice, dict)
            or choice.get('choice') is not True or choice.get('notice_version') != NOTICE_VERSION
            or choice.get('notice_sha256') != NOTICE_SHA256
            or type(choice.get('revision')) is not int or not 1 <= choice['revision'] < 2**53):
        return None
    try:
        born = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
        if born.tzinfo is None or not 0 < born.timestamp() <= time.time() + 300:
            return None
    except ValueError:
        return None
    return data['created_at'], choice['revision']


def day_key(now):
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def blank(now, grant):
    day = day_key(now)
    expiry = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=RETENTION_DAYS)
    return dict(schema_version=1, source=SOURCE, day=day, account_created_at=grant[0],
                consent_revision=grant[1], counts=dict.fromkeys(COUNTERS, 0),
                minutes=[], last_seen=0, tasks={}, complete=True, expires_at=expiry)


def load(raw, now, grant):
    if raw is None or (raw.get('account_created_at'), raw.get('consent_revision')) != grant:
        return blank(now, grant)
    result = deepcopy(raw)
    if (set(result) != set(blank(now, grant)) or result.get('schema_version') != 1 or result.get('source') != SOURCE
            or result.get('day') != day_key(now) or type(result.get('complete')) is not bool
            or type(result.get('counts')) is not dict or set(result['counts']) != set(COUNTERS)
            or any(type(v) is not int or not 0 <= v <= 1_000_000_000 for v in result['counts'].values())
            or type(result.get('minutes')) is not list or len(result['minutes']) > 1440
            or any(type(m) is not int or not 0 <= m < 1440 for m in result['minutes'])
            or len(set(result['minutes'])) != len(result['minutes'])
            or type(result.get('last_seen')) is not int
            or type(result.get('tasks')) is not dict or len(result['tasks']) > MAX_TASKS):
        result = blank(now, grant)
        result['complete'] = False
    return result


def collection(path):
    if len(path) == 2 and path[0] == 'projects':
        return 'projects_created'
    if path[0] != 'projects' or len(path) not in (4, 6, 8):
        return None
    expected = {4: {'chapters', 'scenes', 'tasks', 'canvases', 'story_maps', 'assets', 'reviews'},
                6: {'nodes', 'lines'}, 8: {'items'}}
    return COLLECTIONS.get(path[-2]) if path[-2] in expected[len(path)] else None


def task_path(path):
    return len(path) == 4 and path[0] == 'projects' and path[2] == 'tasks'


def canonical(path):
    return collection(path) is not None or len(path) == 4 and path[0] == 'projects' and path[2] == 'blocks'


def task_fingerprint(row):
    # Hash randomized ciphertext, never task text or a plaintext content hash.
    return digest(row['_bm_private_fields']) if row and '_bm_private_fields' in row else None


def increment(record, key, amount=1):
    value = record['counts'][key] + amount
    if value > 1_000_000_000:
        record['complete'] = False
    else:
        record['counts'][key] = value
