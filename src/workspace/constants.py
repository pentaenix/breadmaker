from __future__ import annotations

APP_SCHEMA_VERSION = 42
PACKAGE_VERSION = 2
MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_PACKAGE_FILES = 50_000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
EXTERNAL_BACKUP_MARKER = '.breadmaker-backup-location.json'
BACKUP_INTEGRITY_SCRUB_SECONDS = 24 * 60 * 60

PORTABLE_PROJECT_MANIFEST_KEYS = {
    'package_format', 'package_version', 'schema_version', 'project_id',
    'name', 'created_at', 'updated_at', 'project_generation',
}
