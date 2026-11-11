"""Read-only ownership accounting for unpublished package-import prefixes.

This is not cleanup authority. Import receipts/plans stay with the account until
all associated staged content has an independently verified retention/purge
outcome. A missing project root must never make its children invisible to census.
"""
import hashlib
import json
import re

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_user_id
from hosted.idempotency import stable_entity_id, stable_project_id

_SHA = re.compile('[0-9a-f]{64}')


def _data(row):
    return dict(row.data) if hasattr(row, 'data') else row


def import_job_scope(path, data, lookup):
    """Validate an exact account job and reservation before claiming its prefix."""
    try:
        uid = require_user_id(path[1])
        op = require_opaque_id(data['operation'], label='operation')
        pid = stable_project_id(op)
        if (len(path) != 4 or path[:1] != ('users',) or path[2] != 'package_imports'
                or path[3] != stable_entity_id('package_', op) or data.get('v') != 1
                or data.get('job_id') != path[3] or data.get('actor') != uid
                or data.get('project_id') != pid or not isinstance(data.get('source_sha256'), str)
                or _SHA.fullmatch(data['source_sha256']) is None
                or not isinstance(data.get('name_override'), str)
                or data.get('phase') not in {'preparing', 'staging', 'ready', 'completed', 'cancelled', 'expired'}):
            raise ValueError()
        reservation = _data(lookup(('hosted_import_operations', op)))
        if (not reservation or reservation.get('user_id') != uid
                or reservation.get('sha256') != data['source_sha256']
                or reservation.get('name_override') != data['name_override']):
            raise ValueError()
        root = _data(lookup(('projects', pid)))
        if root is not None:
            member = _data(lookup(('projects', pid, 'members', uid)))
            if (data['phase'] != 'completed' or not member or member.get('role') != 'owner'
                    or member.get('user_id') != uid):
                raise ValueError()
        return pid
    except (KeyError, TypeError, IndexError, ValueError, HostedValidationError) as exc:
        raise DocumentConflict('HOSTED_IMPORT_OWNERSHIP_INVALID') from exc


def import_child_owned(path, data, job_path, job):
    """Classify only the known direct plan/receipt shapes, never arbitrary trees."""
    if path[:4] != job_path or len(path) != 6:
        return False
    if path[4:] == ('receipts', 'head'):
        return (set(data) == {'next_batch', 'chain', 'records', 'images'}
                and all(type(data[key]) is int and data[key] >= 0 for key in ('next_batch', 'records', 'images'))
                and isinstance(data['chain'], str) and _SHA.fullmatch(data['chain']) is not None)
    if path[4:] == ('sources', 'archive'):
        return source_descriptor_valid(data, job)
    if path[4] == 'snapshots':
        return (path[5].isascii() and path[5].isdecimal() and path[5] == str(int(path[5]))
                and snapshot_descriptor_valid(data, job, int(path[5])))
    if path[4] != 'plans' or not path[5].isascii() or not path[5].isdecimal() or path[5] != str(int(path[5])):
        return False
    if (type(data.get('index')) is not int or data['index'] != int(path[5]) or data.get('v') != 1
            or any(data.get(key) != job[key] for key in ('job_id', 'actor', 'project_id', 'source_sha256', 'operation'))
            or not isinstance(data.get('object_sha256'), str) or _SHA.fullmatch(data['object_sha256']) is None):
        return False
    return data.get('object_name') == (f"projects/{job['project_id']}/package-imports/{job['job_id']}/"
                                      f"plans/{data['index']}/{data['object_sha256']}.json.gz")


def source_descriptor_valid(data, job):
    """Account for a retained original only when oversized capture needs it."""
    binding = {'job_id', 'actor', 'project_id', 'source_sha256', 'operation'}
    fields = {'v', *binding, 'object_name', 'object_generation', 'object_sha256', 'object_size'}
    return (job.get('snapshot_format') == 1 and set(data) == fields
            and type(data['v']) is int and data['v'] == 1
            and all(data[key] == job[key] for key in binding)
            and data['object_sha256'] == job['source_sha256']
            and data['object_name'] == (f"projects/{job['project_id']}/package-imports/{job['job_id']}/"
                                        f"sources/{job['source_sha256']}.breadmaker")
            and type(data['object_generation']) is int and data['object_generation'] >= 1
            and type(data['object_size']) is int and 0 < data['object_size'] <= 64 * 1024**2)


def snapshot_descriptor_valid(data, job, index):
    """Strict internal snapshot shape; unknown formats remain census blockers."""
    from hosted.package_import_snapshot import MAX_DECODED_BYTES
    binding = {'job_id', 'actor', 'project_id', 'source_sha256', 'operation'}
    fields = {'v', *binding, 'index', 'first_batch', 'batch_count', 'plan_sha256', 'object_name',
              'object_generation', 'object_sha256', 'object_size', 'decoded_bytes', 'previous_chain', 'chain'}
    try:
        if (job.get('snapshot_format') != 1 or set(data) != fields
                or type(data['v']) is not int or data['v'] != 1
                or type(data['index']) is not int or data['index'] != index
                or not 0 <= index < job['total_source_chunks']
                or any(data[key] != job[key] for key in binding)
                or data['plan_sha256'] != job['plan_sha256']
                or any(not isinstance(data[key], str) or _SHA.fullmatch(data[key]) is None
                       for key in ('object_sha256', 'previous_chain', 'chain', 'plan_sha256'))
                or type(data['first_batch']) is not int or data['first_batch'] < 0
                or type(data['batch_count']) is not int or data['batch_count'] < 1
                or data['first_batch'] + data['batch_count'] > job['total_batches']
                or data['object_name'] != (f"projects/{job['project_id']}/package-imports/{job['job_id']}/"
                                          f"snapshots/{index}/{data['object_sha256']}.json.gz")
                or type(data['object_generation']) is not int or data['object_generation'] < 1
                or type(data['object_size']) is not int or not 0 < data['object_size'] <= MAX_DECODED_BYTES
                or type(data['decoded_bytes']) is not int or not 0 < data['decoded_bytes'] <= MAX_DECODED_BYTES):
            return False
        body = {key: value for key, value in data.items() if key != 'chain'}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return data['chain'] == digest
    except (KeyError, TypeError, ValueError):
        return False
