from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import EXTERNAL_BACKUP_MARKER, HASH_CHUNK_BYTES

def _recovery_workday(value: object) -> str:
    raw=str(value or '').strip()
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00')) if raw else datetime.now(timezone.utc)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date().isoformat()
    except ValueError:
        return datetime.now().astimezone().date().isoformat()


def _recovery_metadata(reason: str, source_updated_at: object) -> dict[str,Any]:
    local=datetime.now().astimezone();offset=local.utcoffset()
    return {
        'version':1,'trigger':str(reason or 'automatic')[:80],
        'workday':_recovery_workday(source_updated_at),
        'timezone_offset_minutes':int(offset.total_seconds()//60) if offset else 0,
        'source_updated_at':str(source_updated_at or ''),'created_at':datetime.now(timezone.utc).isoformat(timespec='microseconds'),
    }


def _physical_disk_identity(path: Path) -> str | None:
    """Best-effort identity of the physical disk containing *path*.

    Returns None when Breadmaker cannot prove the backing physical device.
    A same-filesystem st_dev match is handled separately as a definite same-disk
    signal, but a st_dev mismatch is never treated as proof of different disks.
    """
    path=Path(path).resolve()
    try:
        if sys.platform == "darwin":
            result=subprocess.run(["diskutil","info","-plist",str(path)],capture_output=True,timeout=8)
            if result.returncode==0 and result.stdout:
                info=plistlib.loads(result.stdout)
                parent=str(info.get("ParentWholeDisk") or "").strip()
                device=str(info.get("DeviceIdentifier") or "").strip()
                if parent: return f"mac:{parent}"
                if device:
                    # A whole-disk identifier is already a physical identity.
                    return f"mac:{device}" if "s" not in device[4:] else None
        elif os.name == "nt":
            drive=path.drive.rstrip('\\/:')
            if drive:
                script=(
                    f"$p=Get-Partition -DriveLetter '{drive}' -ErrorAction Stop; "
                    "$d=$p | Get-Disk -ErrorAction Stop; "
                    "Write-Output $d.Number"
                )
                result=subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",script],capture_output=True,text=True,timeout=8)
                if result.returncode==0 and result.stdout.strip(): return f"win:{result.stdout.strip()}"
        elif sys.platform.startswith("linux"):
            findmnt=shutil.which("findmnt")
            lsblk=shutil.which("lsblk")
            if findmnt and lsblk:
                result=subprocess.run([findmnt,"-n","-o","SOURCE","--target",str(path)],capture_output=True,text=True,timeout=8)
                source=result.stdout.strip() if result.returncode==0 else ""
                if source.startswith('/dev/'):
                    source=os.path.realpath(source)
                    current=source
                    # Walk parents until lsblk reports no parent. This also handles
                    # common partition and device-mapper layouts conservatively.
                    for _ in range(8):
                        parent=subprocess.run([lsblk,"-n","-o","PKNAME",current],capture_output=True,text=True,timeout=8)
                        name=parent.stdout.strip().splitlines()[0].strip() if parent.returncode==0 and parent.stdout.strip() else ""
                        if not name: break
                        current=f"/dev/{name}"
                    return f"linux:{os.path.realpath(current)}"
    except (OSError,ValueError,subprocess.SubprocessError,plistlib.InvalidFileException):
        return None
    return None

def _same_physical_disk(a: Path, b: Path) -> bool | None:
    """Return True/False only when the relationship can be established safely."""
    try:
        if os.stat(a).st_dev == os.stat(b).st_dev:
            return True
    except OSError:
        return None
    ida=_physical_disk_identity(a); idb=_physical_disk_identity(b)
    if ida and idb: return ida==idb
    return None

def _volume_metadata(path: Path) -> dict[str, str]:
    """Best-effort stable volume identity + mount root for removable/local destinations."""
    path=Path(path).expanduser().resolve(); fallback={'id':'','root':path.anchor or '/','label':''}
    try:
        if sys.platform=='darwin':
            result=subprocess.run(['diskutil','info','-plist',str(path)],capture_output=True,timeout=8)
            if result.returncode==0 and result.stdout:
                info=plistlib.loads(result.stdout);root=str(info.get('MountPoint') or path.anchor or '/');vid=str(info.get('VolumeUUID') or '').strip();label=str(info.get('VolumeName') or '').strip()
                return {'id':f'mac-volume:{vid}' if vid else '','root':root,'label':label}
        elif os.name=='nt':
            drive=path.drive.rstrip('\\/:')
            if drive:
                script=(f"$v=Get-Volume -DriveLetter '{drive}' -ErrorAction Stop; "
                        "$o=[PSCustomObject]@{UniqueId=$v.UniqueId;Label=$v.FileSystemLabel;Root=($v.DriveLetter+':\\')}; "
                        "$o|ConvertTo-Json -Compress")
                result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=8)
                if result.returncode==0 and result.stdout.strip():
                    data=json.loads(result.stdout.strip());vid=str(data.get('UniqueId') or '').strip();return {'id':f'win-volume:{vid}' if vid else '','root':str(data.get('Root') or path.anchor),'label':str(data.get('Label') or '').strip()}
        elif sys.platform.startswith('linux'):
            findmnt=shutil.which('findmnt')
            if findmnt:
                result=subprocess.run([findmnt,'-J','-o','TARGET,UUID','--target',str(path)],capture_output=True,text=True,timeout=8)
                if result.returncode==0 and result.stdout.strip():
                    data=json.loads(result.stdout);rows=data.get('filesystems') or []
                    if rows:
                        row=rows[0];root=str(row.get('target') or path.anchor or '/');vid=str(row.get('uuid') or '').strip();return {'id':f'linux-volume:{vid}' if vid else '','root':root,'label':Path(root).name}
    except (OSError,ValueError,subprocess.SubprocessError,plistlib.InvalidFileException,json.JSONDecodeError):
        pass
    return fallback


def _mounted_volume_roots() -> list[Path]:
    roots=[]
    try:
        if sys.platform=='darwin':
            roots=[Path('/')]
            volumes=Path('/Volumes')
            if volumes.is_dir():roots.extend(p for p in volumes.iterdir() if p.is_dir())
        elif os.name=='nt':
            script="Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Root"
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=8)
            if result.returncode==0:roots=[Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
        elif sys.platform.startswith('linux'):
            findmnt=shutil.which('findmnt')
            if findmnt:
                result=subprocess.run([findmnt,'-J','-o','TARGET'],capture_output=True,text=True,timeout=8)
                if result.returncode==0:
                    data=json.loads(result.stdout);roots=[Path(str(row.get('target'))) for row in data.get('filesystems') or [] if row.get('target')]
    except Exception:
        roots=[]
    if not roots:roots=[Path(Path.cwd().anchor or '/')]
    out=[]
    for root in roots:
        try:r=root.resolve()
        except OSError:continue
        if r not in out:out.append(r)
    return out


def _destination_default_name(path:Path, volume:dict[str,str]|None=None, *, destination_kind:str='local') -> str:
    # A synced folder is identified by the user-selected folder, not by the host
    # volume label (for example every iCloud/Dropbox folder on macOS might
    # otherwise be named 'Macintosh HD'). Ordinary/removable destinations keep
    # the useful volume-label behavior introduced in 7.4.
    if destination_kind=='synced_folder':
        return (Path(path).name or 'Synced folder')[:120]
    volume=volume or _volume_metadata(path);label=str(volume.get('label') or '').strip();root=str(volume.get('root') or '').strip()
    if label:return label[:120]
    if root:
        name=Path(root).name
        if name:return name[:120]
    return (Path(path).name or 'Backup')[:120]


def _project_change_parts(root:Path) -> list[tuple[str,int,int]]:
    root=Path(root);parts=[]
    for name in ('project.sqlite3','project.sqlite3-wal'):
        path=root/name
        try:
            st=path.stat();parts.append((name,int(st.st_mtime_ns),int(st.st_size)))
        except OSError:pass
    assets=root/'assets'
    if assets.is_dir():
        for path in sorted(assets.rglob('*')):
            if not path.is_file():continue
            try:
                st=path.stat();parts.append(('assets/'+path.relative_to(assets).as_posix(),int(st.st_mtime_ns),int(st.st_size)))
            except OSError:pass
    return parts


def _project_change_token(root:Path) -> str:
    payload=json.dumps(_project_change_parts(root),separators=(',',':'),ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _project_generation(root: Path) -> int:
    """Read the authoritative canonical generation from one project's SQLite DB.

    Current schema-22 projects always carry generation authority. A missing/invalid
    generation returns 0 and is never replaced by timestamp ordering.
    """
    db=Path(root)/'project.sqlite3'
    if not db.is_file():return 0
    try:
        con=sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True,timeout=10)
        try:
            cols={str(r[1]) for r in con.execute('PRAGMA table_info(projects)')}
            if 'project_generation' not in cols:return 0
            row=con.execute('SELECT project_generation FROM projects ORDER BY id LIMIT 1').fetchone()
            value=int(row[0] or 0) if row else 0
            return max(0,value)
        finally:con.close()
    except (sqlite3.Error,OSError,ValueError,TypeError):
        return 0


def _project_change_age_seconds(root:Path) -> float:
    parts=_project_change_parts(root)
    if not parts:return 10**9
    latest=max(mtime for _,mtime,_ in parts)/1_000_000_000
    return max(0.0,datetime.now().timestamp()-latest)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_zip_entry(zf: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with zf.open(name) as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _webp_dimensions(payload:bytes) -> tuple[int,int] | None:
    """Read the canvas-generated WebP dimensions without adding an image dependency."""
    if len(payload)<30 or payload[:4]!=b'RIFF' or payload[8:12]!=b'WEBP':return None
    kind=payload[12:16]
    if kind==b'VP8X' and len(payload)>=30:
        return 1+int.from_bytes(payload[24:27],'little'),1+int.from_bytes(payload[27:30],'little')
    if kind==b'VP8L' and len(payload)>=25 and payload[20]==0x2f:
        b1,b2,b3,b4=payload[21:25]
        width=1+(((b2&0x3f)<<8)|b1)
        height=1+((b4<<6)|((b3&0x0f)<<2)|((b2&0xc0)>>6))
        return width,height
    if kind==b'VP8 ':
        marker=payload.find(b'\x9d\x01\x2a',20,min(len(payload),80))
        if marker>=0 and marker+7<=len(payload):
            width=int.from_bytes(payload[marker+3:marker+5],'little')&0x3fff
            height=int.from_bytes(payload[marker+5:marker+7],'little')&0x3fff
            return width,height
    return None


def _fsync_dir(path: Path) -> None:
    """Best-effort directory durability after an atomic rename."""
    try:
        fd=os.open(str(Path(path)), os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except (OSError, AttributeError):
        pass

def _durable_replace(tmp: Path, destination: Path) -> None:
    os.replace(tmp, destination)
    _fsync_dir(Path(destination).parent)

def _database_summary(db_path: Path) -> dict[str, Any] | None:
    """Read enough project identity to reconstruct disposable workspace metadata."""
    if not Path(db_path).is_file(): return None
    try:
        con=sqlite3.connect(db_path)
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok": return None
            if con.execute("PRAGMA foreign_key_check").fetchone() is not None: return None
            tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'projects' not in tables: return None
            project_cols={str(r[1]) for r in con.execute("PRAGMA table_info(projects)")}
            select_cols=("project_uid," if 'project_uid' in project_cols else '')+"title,created_at,updated_at"+(",project_generation" if 'project_generation' in project_cols else '')
            row=con.execute(f"SELECT {select_cols} FROM projects ORDER BY id LIMIT 1").fetchone()
            if not row: return None
            has_uid='project_uid' in project_cols
            offset=1 if has_uid else 0
            project_uid=str(row[0] or '') if has_uid else ''
            project_generation=int(row[offset+3] or 0) if len(row)>offset+3 else 0
            schema=0
            if 'storage_meta' in tables:
                r=con.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone()
                if r:
                    try:schema=int(r[0])
                    except (TypeError,ValueError):schema=0
            return {'project_uid':project_uid,'name':safe_name(str(row[offset] or 'Recovered Project')),'created_at':str(row[offset+1] or now()),'updated_at':str(row[offset+2] or row[offset+1] or now()),'schema_version':schema,'project_generation':project_generation}
        finally: con.close()
    except sqlite3.Error:
        return None


def _read_external_backup_marker(folder: Path) -> dict[str, Any] | None:
    marker=Path(folder)/EXTERNAL_BACKUP_MARKER
    if not marker.is_file(): return None
    try:
        data=json.loads(marker.read_text(encoding='utf-8'))
        if data.get('kind')!='breadmaker-local-backup' or int(data.get('version') or 0)!=1 or not str(data.get('location_id') or '').strip(): return None
        return data
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return None

def _write_external_backup_marker(folder: Path, location_id: str) -> None:
    marker=Path(folder)/EXTERNAL_BACKUP_MARKER; tmp=marker.with_name(f'.{marker.name}.{uuid.uuid4().hex}.tmp')
    payload={'kind':'breadmaker-local-backup','version':1,'location_id':location_id,'created_at':now()}
    with tmp.open('w',encoding='utf-8') as fh:
        json.dump(payload,fh,ensure_ascii=False,indent=2); fh.write('\n'); fh.flush(); os.fsync(fh.fileno())
    _durable_replace(tmp,marker)

def safe_name(value: str) -> str:
    value = "".join(c for c in value.strip() if c not in "\\/:*?\"<>|")
    return value[:120] or "Untitled Book"


