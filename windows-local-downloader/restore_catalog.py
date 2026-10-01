"""Offline pre-run rollback; usage: restore_catalog.py <v1 backup path>."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from core import RunLock, SafeError
from downloader import config
from migration import validate, fingerprint

def restore(root, backup):
    state=Path(root).resolve()/'_catalog';destination=state/'manuals.db'
    backup=Path(backup).resolve()
    if backup.parent!=state/'backups' or not backup.is_file():raise SafeError('RESTORE_BACKUP_PATH_INVALID')
    with RunLock(state/'run.lock'):
        source=sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True)
        target=None
        try:
            validate(source,'1');digest=fingerprint(source)
            target=sqlite3.connect(destination)
            # Rollback is deliberately allowed only before new discovery/downloads.
            if fingerprint(target)!=digest:raise SafeError('RESTORE_REFUSED_NEW_WORK_WOULD_BE_LOST')
            version=target.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version not in (('1',),('2',)):raise SafeError('UNSUPPORTED_SCHEMA_VERSION')
            validate(target,version[0])
            rescue=state/'backups'/('manuals-before-restore-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.db')
            safety=sqlite3.connect(rescue)
            try:target.backup(safety)
            finally:safety.close()
            check=sqlite3.connect(rescue.as_uri()+'?mode=ro',uri=True)
            try:validate(check,version[0])
            finally:check.close()
            source.backup(target)
            validate(target,'1')
            if fingerprint(target)!=digest:raise SafeError('RESTORE_VALIDATION_FAILED')
            return rescue
        finally:
            source.close()
            if target:target.close()

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise SafeError('USAGE_restore_catalog.py_V1_BACKUP_PATH')
        print('Restored version 1. Pre-restore safety copy:',restore(config()['destination'],sys.argv[1]))
    except (SafeError,OSError,sqlite3.Error):
        print('RESTORE_STOPPED. Keep all backups. No source requests were made.');sys.exit(1)
