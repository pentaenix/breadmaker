"""Read-only retention classification; never authorizes shared policy deletion."""
from dataclasses import fields
import math
from hosted.firestore.ids import require_user_id
from hosted.security.saved_word_identity import WordIdentity, _stamp
from hosted.security.saved_word_policy import WordPolicy

POLICY_PATH = ('admin_operational', 'saved_word_policy_v1')
LEDGER_COLLECTION = 'admin_saved_word_coverage'


def policy_references_user(data, user_id):
    if data is None:
        return False
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'revision', 'entries'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['entries'], list) or len(data['entries']) > 32):
        raise ValueError('WORD_RETENTION_POLICY_UNVERIFIED')
    names = {field.name for field in fields(WordIdentity)}
    entries = []
    for item in data['entries']:
        if not isinstance(item, dict) or set(item) != names:
            raise ValueError('WORD_RETENTION_POLICY_UNVERIFIED')
        require_user_id(item['hosted_user_id'])
        _stamp(item['hosted_created_at'])
        _stamp(item['binding_created_at'])
        if (not isinstance(item['issuer'], str) or not item['issuer'].startswith('identity-platform://')
                or not isinstance(item['subject'], str) or not 1 <= len(item['subject']) <= 256
                or type(item['provider_created_at_ms']) is not int or item['provider_created_at_ms'] <= 0
                or type(item['binding_created_at_epoch']) not in (int, float)
                or not math.isfinite(item['binding_created_at_epoch']) or item['binding_created_at_epoch'] <= 0):
            raise ValueError('WORD_RETENTION_POLICY_UNVERIFIED')
        entries.append(WordIdentity(**item))
    policy = WordPolicy(data['revision'], tuple(entries))
    return any(entry.hosted_user_id == user_id for entry in policy.entries)
