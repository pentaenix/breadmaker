from __future__ import annotations

"""Hostile-image boundary for hosted Breadmaker.

Hosted uploads are structurally identified without invoking an image decoder.  The
server intentionally accepts only PNG/JPEG/WebP at this boundary.  HEIF/HEIC/AVIF
and every other format remain outside the hosted trust surface until an isolated,
throwaway image worker exists.
"""

from dataclasses import dataclass
from pathlib import Path
import zlib

_MIB = 1024 * 1024
HOSTED_DEFAULT_IMAGE_BYTES = 12 * _MIB
HOSTED_HARD_MAX_IMAGE_BYTES = 25 * _MIB
HOSTED_MAX_IMAGE_DIMENSION = 8192
HOSTED_MAX_IMAGE_PIXELS = 20_000_000
HOSTED_SAFE_IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.webp'})
HOSTED_SAFE_IMAGE_MIME = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.webp': 'image/webp',
}


class UnsafeImage(ValueError):
    pass


@dataclass(frozen=True)
class SafeImageInfo:
    extension: str
    media_type: str
    width: int
    height: int
    byte_size: int


def _dimensions_ok(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise UnsafeImage('IMAGE_DIMENSIONS_INVALID')
    if width > HOSTED_MAX_IMAGE_DIMENSION or height > HOSTED_MAX_IMAGE_DIMENSION:
        raise UnsafeImage('IMAGE_DIMENSIONS_TOO_LARGE')
    if width * height > HOSTED_MAX_IMAGE_PIXELS:
        raise UnsafeImage('IMAGE_PIXELS_TOO_LARGE')


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos=8; width=height=0; saw_ihdr=False; saw_iend=False
    while pos + 12 <= len(data):
        length=int.from_bytes(data[pos:pos+4],'big'); chunk_type=data[pos+4:pos+8]
        end=pos+12+length
        if end>len(data): raise UnsafeImage('PNG_CHUNK_INVALID')
        payload=data[pos+8:pos+8+length]; expected=int.from_bytes(data[pos+8+length:end],'big')
        if zlib.crc32(chunk_type+payload)&0xFFFFFFFF != expected: raise UnsafeImage('PNG_CRC_INVALID')
        if not saw_ihdr:
            if chunk_type!=b'IHDR' or length!=13: raise UnsafeImage('PNG_IHDR_INVALID')
            width=int.from_bytes(payload[:4],'big');height=int.from_bytes(payload[4:8],'big');saw_ihdr=True
        if chunk_type==b'IEND':
            if length!=0 or end!=len(data): raise UnsafeImage('PNG_IEND_INVALID')
            saw_iend=True;break
        pos=end
    if not saw_ihdr or not saw_iend: raise UnsafeImage('PNG_STRUCTURE_INVALID')
    return width,height


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b'\xff\xd8':
        return None
    if data[-2:] != b'\xff\xd9':
        raise UnsafeImage('JPEG_EOI_MISSING')
    # SOF markers carrying dimensions.  DHT/DAC/JPG and restart markers are not SOF.
    sof = {m for m in range(0xC0, 0xD0) if m not in {0xC4, 0xC8, 0xCC}}
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if marker in {0xD9, 0xDA}:  # EOI / SOS before a usable SOF is invalid for us.
            break
        if pos + 2 > len(data):
            break
        seg_len = int.from_bytes(data[pos:pos + 2], 'big')
        if seg_len < 2 or pos + seg_len > len(data):
            raise UnsafeImage('JPEG_SEGMENT_INVALID')
        if marker in sof:
            if seg_len < 7:
                raise UnsafeImage('JPEG_SOF_INVALID')
            height = int.from_bytes(data[pos + 3:pos + 5], 'big')
            width = int.from_bytes(data[pos + 5:pos + 7], 'big')
            return width, height
        pos += seg_len
    raise UnsafeImage('JPEG_DIMENSIONS_MISSING')


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        return None
    declared = int.from_bytes(data[4:8], 'little') + 8
    if declared != len(data):
        raise UnsafeImage('WEBP_RIFF_SIZE_INVALID')
    pos=12
    while pos+8<=len(data):
        size=int.from_bytes(data[pos+4:pos+8],'little');end=pos+8+size
        if end>len(data): raise UnsafeImage('WEBP_CHUNK_SIZE_INVALID')
        pos=end+(size&1)
    if pos!=len(data): raise UnsafeImage('WEBP_STRUCTURE_INVALID')
    chunk = data[12:16]
    chunk_size = int.from_bytes(data[16:20], 'little')
    if chunk_size > len(data) - 20:
        raise UnsafeImage('WEBP_CHUNK_SIZE_INVALID')
    if chunk == b'VP8X':
        if chunk_size < 10 or len(data) < 30:
            raise UnsafeImage('WEBP_VP8X_INVALID')
        width = 1 + int.from_bytes(data[24:27], 'little')
        height = 1 + int.from_bytes(data[27:30], 'little')
        return width, height
    if chunk == b'VP8L':
        if chunk_size < 5 or len(data) < 25 or data[20] != 0x2F:
            raise UnsafeImage('WEBP_VP8L_INVALID')
        bits = int.from_bytes(data[21:25], 'little')
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b'VP8 ':
        if chunk_size < 10 or len(data) < 30 or data[23:26] != b'\x9d\x01\x2a':
            raise UnsafeImage('WEBP_VP8_INVALID')
        width = int.from_bytes(data[26:28], 'little') & 0x3FFF
        height = int.from_bytes(data[28:30], 'little') & 0x3FFF
        return width, height
    raise UnsafeImage('WEBP_CHUNK_UNSUPPORTED')


def inspect_hosted_image_bytes(
    data: bytes, claimed_extension: str = '', *, byte_limit: int | None = None
) -> SafeImageInfo:
    if not data:
        raise UnsafeImage('IMAGE_EMPTY')
    limit = min(int(byte_limit or HOSTED_HARD_MAX_IMAGE_BYTES), HOSTED_HARD_MAX_IMAGE_BYTES)
    if len(data) > limit:
        raise UnsafeImage('IMAGE_BYTES_TOO_LARGE')
    detected: tuple[str, tuple[int, int]] | None = None
    for extension, parser in (('.png', _png_dimensions), ('.jpg', _jpeg_dimensions), ('.webp', _webp_dimensions)):
        dimensions = parser(data)
        if dimensions is not None:
            detected = (extension, dimensions)
            break
    if detected is None:
        raise UnsafeImage('IMAGE_FORMAT_UNSUPPORTED')
    extension, (width, height) = detected
    claimed = str(claimed_extension or '').strip().lower()
    if claimed == '.jpeg':
        claimed = '.jpg'
    if claimed and claimed != extension:
        raise UnsafeImage('IMAGE_FORMAT_MISMATCH')
    _dimensions_ok(width, height)
    return SafeImageInfo(extension, HOSTED_SAFE_IMAGE_MIME[extension], width, height, len(data))


def inspect_hosted_image_file(path: Path, claimed_extension: str = '', *, byte_limit: int | None = None) -> SafeImageInfo:
    path = Path(path)
    size = path.stat().st_size
    limit = min(int(byte_limit or HOSTED_HARD_MAX_IMAGE_BYTES), HOSTED_HARD_MAX_IMAGE_BYTES)
    if size > limit:
        raise UnsafeImage('IMAGE_BYTES_TOO_LARGE')
    return inspect_hosted_image_bytes(path.read_bytes(), claimed_extension, byte_limit=limit)


def hosted_server_image_decoding_enabled() -> bool:
    """Hosted server-side image decoding stays off until an isolated worker exists."""
    return False
