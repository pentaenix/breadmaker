from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import stat
import struct
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from security import content_security
PACKAGE_FORMAT = 'breadmaker-review'
PACKAGE_VERSION = 2
MANIFEST_NAME = 'review.json'
MAGIC = b'BREADREVIEW\x00'
HEADER_LIMIT = 16 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_INNER_PACKAGE_BYTES = 384 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_ASSET_BYTES = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
MAX_JSON_DEPTH = 64
MAX_JSON_STRING_BYTES = 4 * 1024 * 1024
MAX_JSON_NODES = 500000
MAX_JSON_INTEGER = (1 << 63) - 1
MAX_PASSPHRASE_CHARS = 1024
MIN_PASSPHRASE_CHARS = 8
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12


class BreadReviewError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _canonical_payload(manifest: dict[str, Any]) -> bytes:
    payload = dict(manifest)
    payload.pop('payload_hash', None)
    return _canonical_json(payload)


def payload_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(manifest)).hexdigest()


def seal(manifest: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(manifest)
    sealed['package_format'] = PACKAGE_FORMAT
    sealed['package_version'] = PACKAGE_VERSION
    sealed['payload_hash'] = payload_hash(sealed)
    return sealed


def _validate_json_shape(value: Any, *, depth: int = 0, _nodes: list[int] | None = None) -> None:
    if _nodes is None:
        _nodes = [0]
    _nodes[0] += 1
    if _nodes[0] > MAX_JSON_NODES:
        raise BreadReviewError('BREADREVIEW_042', 'BreadReview contains too many JSON values.')
    if depth > MAX_JSON_DEPTH:
        raise BreadReviewError('BREADREVIEW_023', 'BreadReview content is nested beyond the safe limit.')
    if isinstance(value, str):
        if len(value.encode('utf-8')) > MAX_JSON_STRING_BYTES:
            raise BreadReviewError('BREADREVIEW_024', 'BreadReview contains an oversized text value.')
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_JSON_INTEGER:
            raise BreadReviewError('BREADREVIEW_042', 'BreadReview contains an integer outside the safe range.')
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BreadReviewError('BREADREVIEW_042', 'BreadReview contains a non-finite number.')
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_shape(item, depth=depth + 1, _nodes=_nodes)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BreadReviewError('BREADREVIEW_025', 'BreadReview object keys must be text.')
            _validate_json_shape(key, depth=depth + 1, _nodes=_nodes)
            _validate_json_shape(item, depth=depth + 1, _nodes=_nodes)
        return
    raise BreadReviewError('BREADREVIEW_026', 'BreadReview contains an unsupported data type.')


def validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise BreadReviewError('BREADREVIEW_001', 'Review manifest must be an object.')
    try:
        version = int(manifest.get('package_version') or 0)
    except (TypeError, ValueError):
        version = 0
    if manifest.get('package_format') != PACKAGE_FORMAT or version != PACKAGE_VERSION:
        raise BreadReviewError('BREADREVIEW_002', 'Unsupported BreadReview package format.')
    _validate_json_shape(manifest)
    required = ('package_id', 'project_id', 'review_id', 'snapshot_id', 'participant_id', 'review', 'participant', 'snapshot', 'feedback')
    for key in required:
        if key not in manifest:
            raise BreadReviewError('BREADREVIEW_003', f'Missing required field: {key}.')
    expected = str(manifest.get('payload_hash') or '')
    if not expected or not hmac.compare_digest(expected, payload_hash(manifest)):
        raise BreadReviewError('BREADREVIEW_004', 'BreadReview package integrity check failed.')
    review = manifest.get('review')
    participant = manifest.get('participant')
    snapshot = manifest.get('snapshot')
    feedback = manifest.get('feedback')
    if not isinstance(review, dict) or not isinstance(participant, dict) or not isinstance(snapshot, dict) or not isinstance(feedback, dict):
        raise BreadReviewError('BREADREVIEW_005', 'BreadReview package content is malformed.')
    if str(review.get('id') or '') != str(manifest.get('review_id') or ''):
        raise BreadReviewError('BREADREVIEW_006', 'Review identity does not match the package header.')
    if str(participant.get('id') or '') != str(manifest.get('participant_id') or ''):
        raise BreadReviewError('BREADREVIEW_007', 'Participant identity does not match the package header.')
    if str(participant.get('review_id') or '') != str(manifest.get('review_id') or ''):
        raise BreadReviewError('BREADREVIEW_039', 'Participant Review identity does not match the package header.')
    if str(snapshot.get('id') or '') != str(manifest.get('snapshot_id') or ''):
        raise BreadReviewError('BREADREVIEW_008', 'Snapshot identity does not match the package header.')
    for key in ('chapters', 'scenes', 'blocks'):
        if not isinstance(snapshot.get(key, []), list):
            raise BreadReviewError('BREADREVIEW_009', f'Snapshot {key} must be a list.')
    if content_security.find_unsafe_review_snapshot_html(snapshot) is not None:
        raise BreadReviewError('BREADREVIEW_043', 'BreadReview contains unsafe manuscript HTML.')
    for key in ('anchors', 'highlights', 'comments', 'suggestions', 'answers', 'events'):
        if not isinstance(feedback.get(key, []), list):
            raise BreadReviewError('BREADREVIEW_010', f'Feedback {key} must be a list.')
    if not isinstance(manifest.get('questions', []), list) or not isinstance(manifest.get('assets', {}), dict):
        raise BreadReviewError('BREADREVIEW_011', 'Questions or assets are malformed.')
    return manifest


def _safe_member_name(name: str) -> bool:
    if not name or '\x00' in name or '\\' in name or name.startswith('/'):
        return False
    parts = PurePosixPath(name).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        return False
    return all(ord(ch) >= 32 and ord(ch) != 127 for ch in name)


def _validate_zip(z: zipfile.ZipFile, *, max_asset_bytes: int = MAX_ASSET_BYTES) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise BreadReviewError('BREADREVIEW_012', 'BreadReview package contains too many files.')
    names = [str(info.filename) for info in infos]
    if len(names) != len(set(names)):
        raise BreadReviewError('BREADREVIEW_027', 'BreadReview package contains duplicate file names.')
    expanded = sum(int(info.file_size) for info in infos)
    if expanded > MAX_EXPANDED_BYTES:
        raise BreadReviewError('BREADREVIEW_013', 'BreadReview package expands beyond the safe limit.')
    if MANIFEST_NAME not in names:
        raise BreadReviewError('BREADREVIEW_014', 'This is not a BreadReview package.')
    if z.getinfo(MANIFEST_NAME).file_size > MAX_MANIFEST_BYTES:
        raise BreadReviewError('BREADREVIEW_015', 'BreadReview manifest is too large.')
    for info in infos:
        name = str(info.filename)
        if not _safe_member_name(name):
            raise BreadReviewError('BREADREVIEW_016', 'BreadReview package contains an unsafe file path.')
        unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise BreadReviewError('BREADREVIEW_028', 'BreadReview package cannot contain symbolic links.')
        compressed = int(info.compress_size)
        expanded_size = int(info.file_size)
        if compressed == 0 and expanded_size > 0:
            raise BreadReviewError('BREADREVIEW_029', 'BreadReview package contains an invalid compressed entry.')
        if compressed > 0 and expanded_size > compressed * MAX_COMPRESSION_RATIO and expanded_size > 1024 * 1024:
            raise BreadReviewError('BREADREVIEW_030', 'BreadReview package exceeds the safe compression ratio.')
        if name == MANIFEST_NAME:
            continue
        if not name.startswith('assets/'):
            raise BreadReviewError('BREADREVIEW_016', 'BreadReview package contains an unexpected file.')
        if expanded_size > min(int(max_asset_bytes), MAX_ASSET_BYTES):
            raise BreadReviewError('BREADREVIEW_017', 'A BreadReview asset exceeds the safe size limit.')


def _validated_passphrase(passphrase: str | None, *, for_creation: bool = False) -> str:
    if passphrase is None:
        return ''
    if not isinstance(passphrase, str):
        raise BreadReviewError('BREADREVIEW_031', 'BreadReview passphrase is invalid.')
    if len(passphrase) > MAX_PASSPHRASE_CHARS:
        raise BreadReviewError('BREADREVIEW_031', 'BreadReview passphrase is too long.')
    if for_creation and passphrase and len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise BreadReviewError('BREADREVIEW_032', f'BreadReview passphrase must be at least {MIN_PASSPHRASE_CHARS} characters.')
    return passphrase


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    try:
        return hashlib.scrypt(passphrase.encode('utf-8'), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=128 * 1024 * 1024)
    except (ValueError, MemoryError) as exc:
        raise BreadReviewError('BREADREVIEW_033', 'BreadReview key-derivation parameters are invalid.') from exc


def _encode_inner(manifest: dict[str, Any], assets: dict[str, Path | bytes] | None = None, preserved_assets: dict[str, bytes] | None = None) -> bytes:
    sealed = validate_manifest(seal(manifest))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(MANIFEST_NAME, json.dumps(sealed, ensure_ascii=False, separators=(',', ':')))
        for package_path, source in sorted((assets or {}).items()):
            package_path = str(package_path)
            if not _safe_member_name(package_path) or not package_path.startswith('assets/'):
                raise BreadReviewError('BREADREVIEW_021', 'Invalid BreadReview asset path.')
            if isinstance(source,(bytes,bytearray,memoryview)):
                data=bytes(source)
                if len(data)>MAX_ASSET_BYTES:
                    raise BreadReviewError('BREADREVIEW_017', 'A BreadReview asset exceeds the safe size limit.')
                z.writestr(package_path,data)
                continue
            source_path=Path(source)
            if not source_path.is_file():
                continue
            if source_path.stat().st_size > MAX_ASSET_BYTES:
                raise BreadReviewError('BREADREVIEW_017', 'A BreadReview asset exceeds the safe size limit.')
            z.write(source_path, package_path)
        for package_path, data in sorted((preserved_assets or {}).items()):
            if not _safe_member_name(package_path) or not package_path.startswith('assets/'):
                raise BreadReviewError('BREADREVIEW_021', 'Invalid BreadReview asset path.')
            if len(data) > MAX_ASSET_BYTES:
                raise BreadReviewError('BREADREVIEW_017', 'A BreadReview asset exceeds the safe size limit.')
            z.writestr(package_path, data)
    payload = buf.getvalue()
    if len(payload) > MAX_INNER_PACKAGE_BYTES:
        raise BreadReviewError('BREADREVIEW_034', 'BreadReview payload exceeds the safe size limit.')
    # Validate our own output through the complete hostile-input and authority path
    # before any atomic replace can publish it.
    _decode_inner(payload)
    return payload


def _decode_inner(payload: bytes, *, max_asset_bytes: int = MAX_ASSET_BYTES) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(payload) > MAX_INNER_PACKAGE_BYTES:
        raise BreadReviewError('BREADREVIEW_034', 'BreadReview payload exceeds the safe size limit.')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            _validate_zip(z, max_asset_bytes=max_asset_bytes)
            try:
                raw = z.read(MANIFEST_NAME)
                manifest = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise BreadReviewError('BREADREVIEW_020', 'BreadReview manifest is not valid JSON.') from exc
            assets = {info.filename: z.read(info.filename) for info in z.infolist() if info.filename != MANIFEST_NAME}
    except zipfile.BadZipFile as exc:
        raise BreadReviewError('BREADREVIEW_014', 'BreadReview payload is not a valid package.') from exc
    manifest = validate_manifest(manifest)
    asset_map=manifest.get('assets') or {}
    expected=set()
    for logical_name, package_path in asset_map.items():
        if not isinstance(logical_name,str) or not isinstance(package_path,str) or not _safe_member_name(package_path) or not package_path.startswith('assets/'):
            raise BreadReviewError('BREADREVIEW_040', 'BreadReview asset manifest is malformed.')
        if package_path in expected:
            raise BreadReviewError('BREADREVIEW_040', 'BreadReview asset manifest contains duplicate package paths.')
        expected.add(package_path)
    if expected != set(assets):
        raise BreadReviewError('BREADREVIEW_041', 'BreadReview asset inventory does not match the manifest.')
    return manifest, assets


def _header_bytes(encrypted: bool, *, salt: bytes | None = None, nonce: bytes | None = None) -> bytes:
    header: dict[str, Any] = {
        'package_format': PACKAGE_FORMAT,
        'package_version': PACKAGE_VERSION,
        'payload_encoding': 'zip',
        'encrypted': bool(encrypted),
    }
    if encrypted:
        header.update({
            'cipher': 'AES-256-GCM',
            'kdf': 'scrypt',
            'scrypt_n': SCRYPT_N,
            'scrypt_r': SCRYPT_R,
            'scrypt_p': SCRYPT_P,
            'salt': (salt or b'').hex(),
            'nonce': (nonce or b'').hex(),
        })
    return _canonical_json(header)


def _validate_envelope_header(header_bytes: bytes) -> dict[str, Any]:
    try:
        header = json.loads(header_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.') from exc
    if not isinstance(header, dict):
        raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
    if (
        header.get('package_format') != PACKAGE_FORMAT
        or type(header.get('package_version')) is not int
        or header.get('package_version') != PACKAGE_VERSION
        or header.get('payload_encoding') != 'zip'
    ):
        raise BreadReviewError('BREADREVIEW_002', 'Unsupported BreadReview package format.')
    encrypted = header.get('encrypted')
    if type(encrypted) is not bool:
        raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
    allowed = {'package_format', 'package_version', 'payload_encoding', 'encrypted'}
    if encrypted:
        allowed |= {'cipher', 'kdf', 'scrypt_n', 'scrypt_r', 'scrypt_p', 'salt', 'nonce'}
        if header.get('cipher') != 'AES-256-GCM' or header.get('kdf') != 'scrypt':
            raise BreadReviewError('BREADREVIEW_036', 'Unsupported BreadReview encryption parameters.')
        if any(type(header.get(key)) is not int for key in ('scrypt_n', 'scrypt_r', 'scrypt_p')):
            raise BreadReviewError('BREADREVIEW_036', 'Unsupported BreadReview encryption parameters.')
        if (header.get('scrypt_n'), header.get('scrypt_r'), header.get('scrypt_p')) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            raise BreadReviewError('BREADREVIEW_036', 'Unsupported BreadReview encryption parameters.')
        try:
            salt = bytes.fromhex(str(header.get('salt') or ''))
            nonce = bytes.fromhex(str(header.get('nonce') or ''))
        except ValueError as exc:
            raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.') from exc
        if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
            raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
    if set(header) != allowed:
        raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header contains unsupported fields.')
    return header


def _parse_envelope(raw: bytes) -> tuple[dict[str, Any], bytes, bytes]:
    minimum = len(MAGIC) + 4
    if len(raw) < minimum or raw[:len(MAGIC)] != MAGIC:
        raise BreadReviewError('BREADREVIEW_002', 'Unsupported BreadReview package format.')
    header_len = struct.unpack('>I', raw[len(MAGIC):minimum])[0]
    if header_len <= 0 or header_len > HEADER_LIMIT or minimum + header_len >= len(raw):
        raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
    header_bytes = raw[minimum:minimum + header_len]
    header = _validate_envelope_header(header_bytes)
    payload = raw[minimum + header_len:]
    if not payload or len(payload) > MAX_INNER_PACKAGE_BYTES + 32:
        raise BreadReviewError('BREADREVIEW_034', 'BreadReview payload exceeds the safe size limit.')
    return header, header_bytes, payload


def inspect(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise BreadReviewError('BREADREVIEW_018', 'BreadReview file does not exist.')
    size = path.stat().st_size
    if size > MAX_PACKAGE_BYTES:
        raise BreadReviewError('BREADREVIEW_019', 'BreadReview package exceeds the safe size limit.')
    minimum = len(MAGIC) + 4
    try:
        with path.open('rb') as fh:
            prefix = fh.read(minimum)
            if len(prefix) != minimum or prefix[:len(MAGIC)] != MAGIC:
                raise BreadReviewError('BREADREVIEW_002', 'Unsupported BreadReview package format.')
            header_len = struct.unpack('>I', prefix[len(MAGIC):])[0]
            if header_len <= 0 or header_len > HEADER_LIMIT or minimum + header_len >= size:
                raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
            header_bytes = fh.read(header_len)
            if len(header_bytes) != header_len:
                raise BreadReviewError('BREADREVIEW_035', 'BreadReview envelope header is invalid.')
    except OSError as exc:
        raise BreadReviewError('BREADREVIEW_018', 'BreadReview file could not be read.') from exc
    payload_size = size - minimum - header_len
    if payload_size <= 0 or payload_size > MAX_INNER_PACKAGE_BYTES + 32:
        raise BreadReviewError('BREADREVIEW_034', 'BreadReview payload exceeds the safe size limit.')
    header = _validate_envelope_header(header_bytes)
    return {'package_format': PACKAGE_FORMAT, 'package_version': PACKAGE_VERSION, 'encrypted': bool(header['encrypted'])}


def _read_all(path: Path, passphrase: str | None = None, *, max_asset_bytes: int = MAX_ASSET_BYTES) -> tuple[dict[str, Any], dict[str, bytes], bool]:
    path = Path(path)
    if not path.is_file():
        raise BreadReviewError('BREADREVIEW_018', 'BreadReview file does not exist.')
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise BreadReviewError('BREADREVIEW_019', 'BreadReview package exceeds the safe size limit.')
    raw = path.read_bytes()
    header, header_bytes, payload = _parse_envelope(raw)
    encrypted = bool(header['encrypted'])
    if encrypted:
        passphrase = _validated_passphrase(passphrase)
        if not passphrase:
            raise BreadReviewError('BREADREVIEW_037', 'BreadReview passphrase is required.')
        salt = bytes.fromhex(header['salt'])
        nonce = bytes.fromhex(header['nonce'])
        key = _derive_key(passphrase, salt, int(header['scrypt_n']), int(header['scrypt_r']), int(header['scrypt_p']))
        try:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            payload = AESGCM(key).decrypt(nonce, payload, header_bytes)
        except InvalidTag as exc:
            raise BreadReviewError('BREADREVIEW_038', 'BreadReview could not be decrypted. The passphrase is wrong or the file was modified.') from exc
    manifest, assets = _decode_inner(payload, max_asset_bytes=max_asset_bytes)
    return manifest, assets, encrypted


def read(path: Path, passphrase: str | None = None, *, max_asset_bytes: int = MAX_ASSET_BYTES) -> dict[str, Any]:
    manifest, _assets, _encrypted = _read_all(path, passphrase, max_asset_bytes=max_asset_bytes)
    return manifest


def read_asset(path: Path, package_path: str, passphrase: str | None = None, *, max_asset_bytes: int = MAX_ASSET_BYTES) -> bytes:
    package_path = str(package_path or '')
    if not _safe_member_name(package_path) or not package_path.startswith('assets/'):
        raise BreadReviewError('BREADREVIEW_021', 'Invalid BreadReview asset path.')
    _manifest, assets, _encrypted = _read_all(Path(path), passphrase, max_asset_bytes=max_asset_bytes)
    if package_path not in assets:
        raise BreadReviewError('BREADREVIEW_022', 'BreadReview asset is missing.')
    return assets[package_path]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open('wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def _encode_envelope(inner: bytes, passphrase: str | None) -> bytes:
    passphrase = _validated_passphrase(passphrase, for_creation=True)
    if passphrase:
        salt = os.urandom(SALT_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        header_bytes = _header_bytes(True, salt=salt, nonce=nonce)
        key = _derive_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        payload = AESGCM(key).encrypt(nonce, inner, header_bytes)
    else:
        header_bytes = _header_bytes(False)
        payload = inner
    data = MAGIC + struct.pack('>I', len(header_bytes)) + header_bytes + payload
    if len(data) > MAX_PACKAGE_BYTES:
        raise BreadReviewError('BREADREVIEW_019', 'BreadReview package exceeds the safe size limit.')
    return data


def write_new(path: Path, manifest: dict[str, Any], assets: dict[str, Path | bytes], passphrase: str | None = None) -> None:
    inner = _encode_inner(manifest, assets=assets)
    _atomic_write(Path(path), _encode_envelope(inner, passphrase))
    # Publication is not considered complete until the bytes can be inspected and read.
    inspect(path)
    read(path, passphrase)


def rewrite(path: Path, manifest: dict[str, Any], passphrase: str | None = None) -> None:
    path = Path(path)
    _old_manifest, preserved_assets, encrypted = _read_all(path, passphrase)
    if encrypted and not passphrase:
        raise BreadReviewError('BREADREVIEW_037', 'BreadReview passphrase is required.')
    inner = _encode_inner(manifest, preserved_assets=preserved_assets)
    _atomic_write(path, _encode_envelope(inner, passphrase if encrypted else None))
    read(path, passphrase if encrypted else None)
