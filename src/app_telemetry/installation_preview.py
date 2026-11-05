"""Synthetic-only local installation authority. No runtime wiring or transport.

The explicit database must be in a trusted, private scratch directory. It is
installation metadata, never project data or a portable project-package member.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import uuid4

MINUTE = 60_000
WINDOW = 15 * MINUTE
DAY = 24 * 60 * MINUTE
FEATURES = ('manuscript', 'canvas', 'maps', 'tasks', 'reviews')
NOTICE = 'installation-synthetic-preview-2'
APPLICATION_ID = 0x424D5450


@dataclass(frozen=True)
class SyntheticBatch:
    reservation: str
    body: bytes


class SyntheticInstallationTelemetry:
    """Explicit scratch-only model shared by processes opening the same database.

    No connection to Workspace, app composition, browser activity or a network.
    Storage/lock errors discard observations rather than escape to an editor.
    """

    def __init__(self, database: Path, *, synthetic_only: bool, version='0.0.0',
                 clock=lambda: int(time.time() * 1000)):
        import re
        if synthetic_only is not True:
            raise ValueError('SYNTHETIC_ONLY_REQUIRED')
        if not isinstance(version, str) or not re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}', version):
            raise ValueError('VERSION_INVALID')
        self.database, self.version, self.clock = Path(database), version, clock
        self.disabled = False
        self._private_storage(require_file=False)
        try:
            # No implicit application-home selection or directory creation.
            fd = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            if self.database.is_symlink():
                raise ValueError('SCRATCH_DATABASE_SYMLINK') from None
        with self._transaction() as db:
            schema = db.execute('PRAGMA user_version').fetchone()[0]
            application = db.execute('PRAGMA application_id').fetchone()[0]
            populated = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone())
            if (schema, application) != (2, APPLICATION_ID) and (schema != 0 or application != 0 or populated):
                raise ValueError('SCRATCH_SCHEMA_UNSUPPORTED')
            db.execute('CREATE TABLE IF NOT EXISTS consent (singleton INTEGER PRIMARY KEY CHECK(singleton=1), choice TEXT NOT NULL, notice TEXT NOT NULL, token TEXT, decided INTEGER, last_activity INTEGER, last_attempt INTEGER)')
            db.execute("INSERT OR IGNORE INTO consent VALUES (1,'pending',?,NULL,NULL,NULL,NULL)", (NOTICE,))
            db.execute('CREATE TABLE IF NOT EXISTS windows (bucket INTEGER PRIMARY KEY, version TEXT NOT NULL, active INTEGER NOT NULL, sessions INTEGER NOT NULL, manuscript INTEGER NOT NULL, canvas INTEGER NOT NULL, maps INTEGER NOT NULL, tasks INTEGER NOT NULL, reviews INTEGER NOT NULL, reservation TEXT)')
            db.execute('PRAGMA user_version=2')
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')

    @contextmanager
    def _transaction(self):
        self._private_storage()
        # Runtime operations must never recreate a database removed by erasure.
        db = sqlite3.connect(self.database.absolute().as_uri()+'?mode=rw', uri=True, timeout=.05)
        try:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _private_storage(self, *, require_file=True):
        # POSIX preview only. Do not assume Unix mode bits enforce Windows ACLs.
        if os.name != 'posix':
            raise ValueError('PRIVATE_STORAGE_PLATFORM_UNREVIEWED')
        parent = self.database.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError('PRIVATE_DIRECTORY_REQUIRED')
        try:
            file = self.database.lstat()
        except FileNotFoundError:
            if not require_file:
                return
            raise
        if not stat.S_ISREG(file.st_mode) or file.st_uid != os.getuid() or file.st_mode & 0o077 or file.st_nlink != 1:
            raise ValueError('PRIVATE_DATABASE_REQUIRED')

    def _now(self):
        value = self.clock()
        if type(value) is not int or not 0 <= value <= 8_640_000_000_000_000:
            raise ValueError('CLOCK_INVALID')
        return value

    def _prune(self, db, now):
        db.execute('DELETE FROM windows WHERE bucket <= ?', (now - DAY,))
        db.execute('DELETE FROM windows WHERE bucket NOT IN (SELECT bucket FROM windows ORDER BY bucket DESC LIMIT 96)')
        db.execute('UPDATE consent SET last_activity=NULL WHERE last_activity<=?', (now - 30 * MINUTE,))
        db.execute('UPDATE consent SET last_attempt=NULL WHERE last_attempt<=?', (now - WINDOW,))

    def _granted(self, db):
        row = db.execute('SELECT choice,notice,token FROM consent WHERE singleton=1').fetchone()
        return bool(not self.disabled and row and row[0] == 'granted' and row[1] == NOTICE and row[2])

    def choice(self):
        if self.disabled:
            return 'declined'
        try:
            with self._transaction() as db:
                row = db.execute('SELECT choice,notice FROM consent WHERE singleton=1').fetchone()
                return row[0] if row and row[1] == NOTICE and row[0] in ('pending','declined','granted') else 'pending'
        except (sqlite3.Error, OSError, ValueError):
            return 'declined'

    def maintain_synthetic_retention(self):
        """Explicit maintenance tick; no process/thread/scheduler starts implicitly."""
        try:
            with self._transaction() as db:
                self._prune(db, self._now())
            return True
        except (sqlite3.Error, OSError, ValueError):
            return False

    def erase_synthetic_state(self):
        """Logical erasure with a durable refusal tombstone, never forensic erasure."""
        try:
            with self._transaction() as db:
                db.execute('DELETE FROM windows')
                # Keep only refusal, notice and cooldown (to prevent budget reset).
                db.execute("UPDATE consent SET choice='declined',notice=?,token=NULL,decided=NULL,last_activity=NULL WHERE singleton=1", (NOTICE,))
            return True
        except (sqlite3.Error, OSError, ValueError):
            self.disabled = True
            return False

    def set_consent(self, allowed: bool):
        if type(allowed) is not bool or self.disabled:
            return False
        try:
            with self._transaction() as db:
                now = self._now()
                # Repeated grant retains the token/session; withdrawal always purges.
                if allowed and self._granted(db):
                    return True
                db.execute('DELETE FROM windows')
                db.execute('UPDATE consent SET choice=?,notice=?,token=?,decided=?,last_activity=NULL WHERE singleton=1',
                           ('granted' if allowed else 'declined', NOTICE, str(uuid4()) if allowed else None, now))
            return True
        except (sqlite3.Error, OSError, ValueError):
            # No further work from this instance after a failed preference write.
            self.disabled = True
            return False

    def record_synthetic_activity(self, feature: str, *, foreground: bool):
        if self.disabled or type(feature) is not str or feature not in FEATURES or foreground is not True:
            return False
        try:
            with self._transaction() as db:
                if not self._granted(db):
                    return False
                now = self._now()
                self._prune(db, now)
                minute = now // MINUTE * MINUTE
                bucket = minute // WINDOW * WINDOW
                bit = 1 << ((minute - bucket) // MINUTE)
                previous, attempted = db.execute('SELECT last_activity,last_attempt FROM consent WHERE singleton=1').fetchone()
                # Never accept time-travelling activity after a wall-clock rollback.
                if (previous is not None and minute < previous) or (attempted is not None and now < attempted):
                    return False
                session = int(previous is None or minute - previous >= 30 * MINUTE)
                db.execute('INSERT OR IGNORE INTO windows VALUES (?,?,0,0,0,0,0,0,0,NULL)', (bucket, self.version))
                # feature is selected from a closed enum above, never caller SQL.
                db.execute(f'UPDATE windows SET active=active|?,sessions=sessions+?,{feature}={feature}|? WHERE bucket=?', (bit, session, bit, bucket))
                db.execute('UPDATE consent SET last_activity=? WHERE singleton=1', (minute,))
                self._prune(db, now)
            return True
        except (sqlite3.Error, OSError, ValueError):
            return False

    def _rows(self, db, now, *, completed=False):
        self._prune(db, now)
        token = db.execute('SELECT token FROM consent WHERE singleton=1').fetchone()[0]
        where = 'WHERE bucket+?<=?' if completed else ''
        args = (WINDOW, now) if completed else ()
        rows = db.execute(f'SELECT bucket,active,sessions,manuscript,canvas,maps,tasks,reviews,version FROM windows {where} ORDER BY bucket', args).fetchall()
        return [dict(schema='bm-installation-synthetic-v2', installation_id=token,
                     notice_version=NOTICE, app_version=row[8], bucket_utc_ms=row[0],
                     sessions=row[2], active_minutes=row[1].bit_count(),
                     workspace_minutes=dict(zip(FEATURES, (value.bit_count() for value in row[3:8])))) for row in rows]

    def snapshot(self):
        try:
            with self._transaction() as db:
                return self._rows(db, self._now()) if self._granted(db) else []
        except (sqlite3.Error, OSError, ValueError):
            return []

    def prepare_synthetic_batch(self):
        """Reserve the installation budget; returns data only, never sends anything."""
        try:
            with self._transaction() as db:
                if not self._granted(db):
                    return None
                now = self._now()
                last = db.execute('SELECT last_attempt FROM consent WHERE singleton=1').fetchone()[0]
                if last is not None and now - last < WINDOW:
                    return None
                rows = self._rows(db, now, completed=True)[:4]
                if not rows:
                    return None
                body = json.dumps(dict(schema='bm-installation-batch-synthetic-v2', reports=rows), separators=(',', ':')).encode()
                if len(body) > 4096:
                    return None
                reservation = str(uuid4())
                db.execute('UPDATE consent SET last_attempt=? WHERE singleton=1', (now,))
                db.execute('UPDATE windows SET reservation=NULL')
                db.executemany('UPDATE windows SET reservation=? WHERE bucket=?', ((reservation, row['bucket_utc_ms']) for row in rows))
                return SyntheticBatch(reservation, body)
        except (sqlite3.Error, OSError, ValueError):
            return None

    def acknowledge_synthetic_batch(self, reservation: str):
        """Only the current reservation can remove rows; revoked batches are stale."""
        if not isinstance(reservation, str) or len(reservation) != 36:
            return False
        try:
            with self._transaction() as db:
                if not self._granted(db):
                    return False
                # Expired data is never retained merely to await an acknowledgement.
                self._prune(db, self._now())
                cursor = db.execute('DELETE FROM windows WHERE reservation=?', (reservation,))
                return cursor.rowcount > 0
        except (sqlite3.Error, OSError, ValueError):
            return False
