"""Explicit offline v1 -> v2 promotion. Never imports a network adapter."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from core import SafeError

TABLES = ('folders', 'folder_pages', 'folder_items', 'manuals', 'file_refs', 'budget')

def fingerprint(db):
    """Streaming digest of every preserved value, including timestamps and paths."""
    digest = hashlib.sha256()
    for table in TABLES:
        digest.update(table.encode())
        for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid'):
            digest.update(json.dumps(tuple(row),ensure_ascii=True,separators=(',',':')).encode())
            digest.update(b'\n')
    return digest.hexdigest()

def validate(db, version):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise SafeError('CATALOG_INTEGRITY_FAILED')
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise SafeError('CATALOG_RELATIONSHIPS_INVALID')
    if db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() != (version,):
        raise SafeError('UNSUPPORTED_SCHEMA_VERSION')
    # These are the exact v1 protections we know how to migrate.
    reference=sqlite3.connect(':memory:')
    try:
        reference.executescript(Path(__file__).with_name('schema.sql').read_text())
        expected=dict(reference.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        if version == '2': expected.pop('budget_limit')
        actual=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        if actual != expected: raise SafeError('CATALOG_PROTECTIONS_UNRECOGNIZED')
        # Reject changed columns/constraints instead of migrating an unknown catalog.
        for table in (*TABLES,'meta'):
            if db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()!=reference.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                raise SafeError('CATALOG_SCHEMA_UNRECOGNIZED')
            if db.execute('PRAGMA table_info('+table+')').fetchall() != reference.execute('PRAGMA table_info('+table+')').fetchall():
                raise SafeError('CATALOG_SCHEMA_UNRECOGNIZED')
    finally: reference.close()
    if db.execute("""SELECT 1 FROM manuals m LEFT JOIN budget b ON b.file_key=m.key
        WHERE m.status='verified' AND (b.state IS NULL OR b.state!='success'
        OR m.sha256 IS NULL OR length(m.sha256)!=64 OR m.actual_size<=0
        OR m.signature_ok!=1 OR m.verification!='parsed_pdf') LIMIT 1""").fetchone():
        raise SafeError('CATALOG_SUCCESS_INVALID')
    if version=='1' and db.execute('SELECT count(*) FROM budget').fetchone()[0]>10:
        raise SafeError('CATALOG_TEST_BUDGET_INVALID')
    if version=='2' and db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()!=('full',):
        raise SafeError('CATALOG_MODE_INVALID')

def verified_backup(source, state):
    directory=state/'backups'; directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path=directory/('manuals-v1-before-v2-'+stamp+'.db')
    try:
        target=sqlite3.connect(path)
        try: source.backup(target); target.commit()
        finally: target.close()
        with path.open('rb') as handle: os.fsync(handle.fileno())
        check=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            validate(check,'1')
            digest=fingerprint(check)
            if digest!=fingerprint(source): raise SafeError('BACKUP_STATE_MISMATCH')
        finally: check.close()
        return path,digest
    except (OSError,sqlite3.Error,SafeError):
        raise SafeError('BACKUP_FAILED_OR_UNREADABLE_ORIGINAL_UNCHANGED') from None

def promote(root, fault=lambda stage: None):
    """Caller holds RunLock. Fault hook is exclusively for offline crash tests."""
    state=Path(root).resolve()/'_catalog'; path=state/'manuals.db'
    if not path.is_file(): raise SafeError('CATALOG_NOT_FOUND_RUN_SETUP_FIRST')
    db=sqlite3.connect(path,timeout=30)
    try:
        version=db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version==('2',): validate(db,'2'); return {'schema_version':2,'mode':'full','already_promoted':True}
        validate(db,'1'); fault('before_backup')
        backup,digest=verified_backup(db,state); fault('after_backup')
        db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        try:
            validate(db,'1')
            if fingerprint(db)!=digest: raise SafeError('CATALOG_CHANGED_SINCE_BACKUP')
            fault('before_changes')
            db.execute('DROP TRIGGER budget_limit'); fault('after_drop')
            db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
            db.execute("INSERT INTO meta VALUES('mode','full')")
            db.execute("INSERT INTO meta VALUES('migration_backup',?)",(str(backup),))
            db.execute("INSERT INTO meta VALUES('test_successes_at_promotion',?)",(str(db.execute("SELECT count(*) FROM budget WHERE state='success'").fetchone()[0]),))
            db.execute("INSERT INTO meta VALUES('promoted_at',?)",(datetime.now(timezone.utc).isoformat(),))
            validate(db,'2')
            if fingerprint(db)!=digest: raise SafeError('MIGRATION_STATE_CHANGED')
            fault('before_commit'); db.commit()
        except BaseException: db.rollback(); raise
        fault('after_commit')
        return {'schema_version':2,'mode':'full','backup':str(backup),'preserved_state_sha256':digest,'already_promoted':False}
    finally: db.close()
