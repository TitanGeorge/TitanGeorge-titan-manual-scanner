"""Local catalog and crash-safe PDF pipeline. Never logs remote text or URLs."""
from __future__ import annotations
import csv
from contextlib import nullcontext
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone

HARD_LIMIT = 10  # No CLI/config override. Database independently enforces the same ceiling.
logging.getLogger('pypdf').disabled = True

class SafeError(Exception):
    def __init__(self, code, http=None, retry_after=0):
        self.code, self.http, self.retry_after = code, http, retry_after
        super().__init__(code)

class AuthExpired(SafeError):
    def __init__(self): super().__init__('AUTH_EXPIRED_REAUTHENTICATE')

class RatePaused(SafeError):
    def __init__(self,http=429): super().__init__('RATE_LIMIT_PAUSED',http)

class DiskLow(SafeError):
    def __init__(self): super().__init__('DISK_SPACE_PAUSED')

def now(): return datetime.now(timezone.utc).isoformat()

def clean_object(value):
    """Retain complete structural folder metadata except authentication/link material."""
    if isinstance(value, dict):
        return {k: clean_object(v) for k, v in value.items()
                if not re.search(r'nonce|cookie|password|credential|authorization|session|token|secret|resource.?key|link|url', k, re.I)}
    if isinstance(value, list): return [clean_object(x) for x in value]
    if isinstance(value, str) and re.search(r'https?://', value, re.I): return '[remote link omitted]'
    return value

def identity(file, parent=None):
    account = str(file.get('accountId') or (parent or {}).get('accountId') or '')
    target = str((file.get('shortcutDetails') or {}).get('targetId') or file.get('id') or '')
    if not account or not target: raise SafeError('METADATA_IDENTITY_MISSING')
    return account + ':' + target

def effective_mime(file):
    return (file.get('shortcutDetails') or {}).get('targetMimeType') or file.get('type') or file.get('mimeType') or ''

def size_of(value):
    if value is None or isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)): return None
    number = int(value)
    return number if 0 < number <= 9223372036854775807 else None

def safe_component(name, limit=70):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip().rstrip('. ')
    name = re.sub(r'[\ud800-\udfff]', '_', name)
    bounded = ''; units = 0
    for char in name:
        width = 2 if ord(char) > 65535 else 1
        if units + width > limit: break
        bounded += char; units += width
    name = bounded.rstrip('. ') or 'unnamed'
    if re.match(r'^(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', name, re.I): name = '_' + name
    return name

def filename(source, key):
    # Full identity hash protects case-insensitive collisions and duplicate names.
    stem = re.sub(r'\.pdf$', '', str(source), flags=re.I)
    return safe_component(stem, 80) + '--' + hashlib.sha256(key.encode()).hexdigest() + '.pdf'

class RunLock:
    """Kernel lock; automatically released after Windows/process restart."""
    def __init__(self, path): self.path = Path(path); self.handle = None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, 'a+b')
        if self.handle.tell() == 0: self.handle.write(b'0'); self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close(); raise SafeError('ANOTHER_RUN_IS_ACTIVE') from None
        return self
    def __exit__(self, *args): self.handle.close()

class Catalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state = self.root / '_catalog'
        for sub in ('logs', 'reports', 'quarantine'): (self.state / sub).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / 'manuals.db', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='meta' AND type='table'").fetchone()
        if not exists:
            self.db.executescript(Path(__file__).with_name('schema.sql').read_text())
        version=self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not version or version[0] not in ('1','2'):
            self.db.close(); raise SafeError('UNSUPPORTED_SCHEMA_VERSION')
        self.version=int(version[0])
        self.hard_limit=HARD_LIMIT if self.version==1 else None
        self.active=False
    def close(self): self.db.close()
    def event(self, code, key=None, http=None):
        # Only call with locally defined codes, opaque identity hash and numeric HTTP code.
        record = {'at': now(), 'event': code, 'file': hashlib.sha256(key.encode()).hexdigest() if key else None, 'http': http}
        with (self.state / 'logs' / 'events.jsonl').open('a', encoding='utf-8') as f: f.write(json.dumps(record) + '\n')
    def add_folder(self, folder, source_path):
        key = identity(folder)
        with (nullcontext() if self.db.in_transaction else self.db):
            self.db.execute('INSERT OR IGNORE INTO folders(key,object_json,source_path,updated) VALUES(?,?,?,?)',
                            (key, json.dumps(clean_object(folder)), source_path, now()))
        return key
    def add_file(self, file, folder_key, source_path, parent=None):
        key = identity(file, parent)
        account, target = key.split(':', 1)
        source = str(file.get('name') or 'unnamed.pdf')
        local_name = filename(source, key)
        # One source folder (manufacturer) plus stable identity suffix, bounded path.
        folder_label = source_path.split('/')[1] if '/' in source_path else source_path
        path = self.root / safe_component(folder_label, 45) / local_name
        if len(str(path).encode('utf-16-le')) // 2 > 240: raise SafeError('DESTINATION_PATH_TOO_LONG')
        previous=self.db.execute('SELECT * FROM manuals WHERE key=?',(key,)).fetchone()
        incoming_size=size_of(file.get('size'))
        if previous and ((previous['folder_key']==folder_key and previous['request_id']==str(file['id']) and previous['source_filename']!=source) or
                         (incoming_size is not None and previous['expected_size'] is not None and incoming_size!=previous['expected_size'])):
            self.event('SOURCE_METADATA_CHANGED_REVIEW',key)
            raise SafeError('SOURCE_METADATA_CHANGED_REVIEW')
        with (nullcontext() if self.db.in_transaction else self.db):
            self.db.execute('''INSERT OR IGNORE INTO manuals
                (key,file_id,account_id,request_id,source_filename,local_filename,folder_key,source_path,mime,expected_size,local_path,discovered)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (key,target,account,str(file['id']),source,local_name,folder_key,source_path,effective_mime(file),size_of(file.get('size')),str(path),now()))
            self.db.execute('UPDATE manuals SET expected_size=COALESCE(expected_size,?) WHERE key=?', (size_of(file.get('size')),key))
            self.db.execute('INSERT OR IGNORE INTO file_refs VALUES(?,?,?,?)', (key,folder_key,str(file['id']),source))
        return key
    def successes(self): return self.db.execute("SELECT count(*) FROM budget WHERE state='success'").fetchone()[0]
    def reserve(self, key):
        with self.db:
            self.db.execute('INSERT INTO budget VALUES(?,?,?)', (key,'reserved',now()))
            self.db.execute("UPDATE manuals SET status='downloading',started=?,last_error=NULL WHERE key=?", (now(),key))
    def release(self, key, code, http=None, signature=False):
        with self.db:
            self.db.execute("DELETE FROM budget WHERE file_key=? AND state='reserved'", (key,))
            self.db.execute("UPDATE manuals SET status='failed',verification='failed',last_error=?,http_status=?,signature_ok=? WHERE key=?", (code,http,int(signature),key))
        self.event(code,key,http)
    def mark_success(self, key, info, http=None):
        with self.db:
            self.db.execute("UPDATE budget SET state='success' WHERE file_key=?", (key,))
            self.db.execute("""UPDATE manuals SET status='verified',actual_size=?,sha256=?,signature_ok=1,
                verification='parsed_pdf',last_error=NULL,finished=?,http_status=? WHERE key=?""", (info[0],info[1],now(),http,key))
        self.event('PDF_VERIFIED',key,http)
    def status(self):
        counts = dict(self.db.execute('SELECT status,count(*) FROM manuals GROUP BY status').fetchall())
        refs = self.db.execute('SELECT count(*) FROM file_refs').fetchone()[0]
        total = self.db.execute('SELECT count(*) FROM manuals').fetchone()[0]
        sums = self.db.execute("SELECT COALESCE(sum(CASE WHEN status='verified' THEN actual_size ELSE 0 END),0),COALESCE(sum(CASE WHEN status!='verified' THEN expected_size ELSE 0 END),0),sum(CASE WHEN expected_size IS NULL THEN 1 ELSE 0 END),max(finished) FROM manuals").fetchone()
        folder = self.db.execute("SELECT source_path,page,status FROM folders WHERE status!='done' ORDER BY rowid LIMIT 1").fetchone()
        return {'discovered': total, 'pending': counts.get('pending',0), 'downloading': counts.get('downloading',0),
                'verified': counts.get('verified',0), 'needs_review': counts.get('needs_review',0), 'failed': counts.get('failed',0), 'needing_retry': counts.get('failed',0),
                'duplicate_references': refs-total,'verified_bytes':sums[0],'known_remaining_bytes':sums[1],
                'missing_sizes':sums[2] or 0,'last_success':sums[3],'folder_progress':dict(folder) if folder else None,
                'failed_folders':self.db.execute("SELECT count(*) FROM folders WHERE status='failed'").fetchone()[0],
                'available_disk_bytes':shutil.disk_usage(self.root).free,'success_budget_used':self.successes(),
                'hard_limit':self.hard_limit,'limit_reached':self.hard_limit is not None and self.successes() >= self.hard_limit,
                'schema_version':self.version,'mode':'test' if self.version==1 else 'full',
                'crawling_active':self.active,'paused':not self.active,
                'folders_discovered':self.db.execute('SELECT count(*) FROM folders').fetchone()[0],
                'folders_completed':self.db.execute("SELECT count(*) FROM folders WHERE status='done'").fetchone()[0],
                'folders_pending':self.db.execute("SELECT count(*) FROM folders WHERE status='pending'").fetchone()[0]}

    def export(self):
        path = self.state / 'reports' / 'manifest.csv'
        rows = self.db.execute('SELECT * FROM manuals ORDER BY key')
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer = csv.writer(f); writer.writerow([x[0] for x in rows.description])
            for row in rows:
                # Prevent Excel formula execution from untrusted source filenames.
                writer.writerow([("'"+v if isinstance(v,str) and v[:1] in '=+-@\t\r\n' else v) for v in row])
        return path

def disk_check(root, threshold, additional=0):
    if shutil.disk_usage(root).free < threshold + additional: raise DiskLow()

def verify(path, expected=None):
    from pypdf import PdfReader
    try:
        actual = path.stat().st_size
        with path.open('rb') as f:
            if actual == 0 or f.read(5) != b'%PDF-': raise SafeError('NON_PDF_RESPONSE')
            if expected is not None and actual != expected: raise SafeError('EXPECTED_SIZE_MISMATCH')
            f.seek(max(0,actual-2048))
            if b'%%EOF' not in f.read(): raise SafeError('TRUNCATED_PDF')
            f.seek(0); digest = hashlib.file_digest(f, 'sha256').hexdigest()
            f.seek(0); reader = PdfReader(f,strict=True)
            if reader.is_encrypted: raise SafeError('ENCRYPTED_PDF_NEEDS_REVIEW')
            if len(reader.pages) < 1: raise SafeError('INVALID_PDF')
            for page in reader.pages:
                _ = page.mediabox
                content = page.get_contents()
                if content is not None: content.get_data()
        return actual,digest
    except SafeError: raise
    except Exception: raise SafeError('INVALID_PDF') from None

def quarantine(cat, path, key):
    if path.exists():
        # HTML/login bodies may contain session material; never retain them.
        with path.open('rb') as probe:
            is_pdf = probe.read(5) == b'%PDF-'
        if not is_pdf:
            path.unlink(); return
        dest = cat.state / 'quarantine' / (hashlib.sha256(key.encode()).hexdigest() + '-' + str(time.time_ns()) + '.rejected')
        path.rename(dest)

def finalize(cat, row, part, info, http=None):
    final = Path(row['local_path'])
    # Persist validation before rename so crash recovery can distinguish our file from a collision.
    with cat.db:
        cat.db.execute('UPDATE manuals SET actual_size=?,sha256=?,signature_ok=1,verification=? WHERE key=?', (info[0],info[1],'staged',row['key']))
    if final.exists(): raise SafeError('EXISTING_DESTINATION_CONFLICT')
    # Windows os.rename is atomic and refuses to overwrite. POSIX link gives equivalent no-clobber publication.
    if os.name == 'nt': os.rename(part,final)
    else: os.link(part,final); part.unlink()
    cat.mark_success(row['key'],info,http)

def recover(cat):
    for row in cat.db.execute("SELECT * FROM manuals WHERE status='verified'").fetchall():
        try:
            info=verify(Path(row['local_path']),row['expected_size'])
            if info[1]!=row['sha256']: raise SafeError('VERIFIED_FILE_CHANGED')
        except SafeError:
            with cat.db: cat.db.execute("UPDATE manuals SET status='needs_review',verification='local_file_missing_or_changed',last_error='VERIFIED_FILE_NEEDS_REVIEW' WHERE key=?",(row['key'],))
            cat.event('VERIFIED_FILE_NEEDS_REVIEW',row['key'])
    for row in cat.db.execute("SELECT m.* FROM manuals m JOIN budget b ON m.key=b.file_key WHERE b.state='reserved'").fetchall():
        final = Path(row['local_path']); part = Path(str(final)+'.part')
        try:
            if final.exists():
                info=verify(final,row['expected_size'])
                if row['verification']!='staged' or row['sha256']!=info[1]: raise SafeError('EXISTING_DESTINATION_CONFLICT')
                cat.mark_success(row['key'],info,row['http_status'])
            elif part.exists():
                info=verify(part,row['expected_size'])
                if row['verification']!='staged' or row['sha256']!=info[1]: raise SafeError('INTERRUPTED_PART_RETRY')
                finalize(cat,row,part,info,row['http_status'])
            else: raise SafeError('INTERRUPTED_DOWNLOAD_RETRY')
        except SafeError as error:
            quarantine(cat,part,row['key']); cat.release(row['key'],error.code)
    # Unknown orphan parts never become complete without a known reservation and validation.
    for row in cat.db.execute("SELECT * FROM manuals WHERE status!='downloading'").fetchall():
        quarantine(cat,Path(row['local_path']+'.part'),row['key'])

class Pacer:
    def __init__(self, delay, sleep=time.sleep): self.delay=delay; self.sleep=sleep; self.lock=threading.Lock(); self.last=0
    def wait(self):
        with self.lock:
            remaining = self.delay-(time.monotonic()-self.last)
            if remaining > 0: self.sleep(remaining)
            self.last=time.monotonic()

def retry_wait(error, attempt, sleep=time.sleep):
    # Long server Retry-After pauses this run instead of hammering or ignoring the value.
    if error.retry_after > 300: raise RatePaused(error.http)
    sleep(max(error.retry_after, min(60,2**attempt+random.uniform(0,1))))

def download(cat, row, transport, threshold, retries=3, pacer=None, sleep=time.sleep, stop=None):
    part=Path(row['local_path']+'.part'); final=Path(row['local_path'])
    try:
        disk_check(cat.root,threshold,row['expected_size'] or 0)
        if final.exists(): raise SafeError('EXISTING_DESTINATION_CONFLICT')
        final.parent.mkdir(parents=True,exist_ok=True)
        quarantine(cat,part,row['key'])
        for attempt in range(retries):
            response=None
            try:
                if stop is not None and stop.is_set(): raise SafeError('RUN_PAUSED')
                if pacer: pacer.wait()
                with cat.db: cat.db.execute('UPDATE manuals SET attempts=attempts+1 WHERE key=?',(row['key'],))
                response=transport.download(row)
                with cat.db: cat.db.execute('UPDATE manuals SET http_status=? WHERE key=?',(response.status_code,row['key']))
                mime=response.headers.get('Content-Type','').split(';')[0].lower()
                if mime in ('text/html','application/xhtml+xml'): raise SafeError('HTML_RESPONSE_REJECTED',response.status_code)
                if mime and mime not in ('application/pdf','application/octet-stream','binary/octet-stream','application/download','application/x-download'):
                    raise SafeError('UNEXPECTED_CONTENT_TYPE',response.status_code)
                length=size_of(response.headers.get('Content-Length')) if not response.headers.get('Content-Encoding') else None
                disk_check(cat.root,threshold,max(row['expected_size'] or 0,length or 0))
                with part.open('wb') as f:
                    prefix = b''
                    prefix_checked = False
                    for chunk in response.iter_content(1024*1024):
                        if stop is not None and stop.is_set(): raise SafeError('RUN_PAUSED')
                        if not chunk: continue
                        if not prefix_checked:
                            prefix += chunk
                            if len(prefix) < 5: continue
                            head = prefix[:4096].lower()
                            if re.search(br'name=["\']pwd["\']|wp-login|mepr-login|unauthorized-access',head): raise AuthExpired()
                            if not prefix.startswith(b'%PDF-'): raise SafeError('NON_PDF_RESPONSE')
                            chunk=prefix; prefix=b''; prefix_checked=True
                        disk_check(cat.root,threshold,len(chunk)); f.write(chunk)
                    f.flush(); os.fsync(f.fileno())
                actual=part.stat().st_size
                with cat.db: cat.db.execute('UPDATE manuals SET actual_size=?,signature_ok=? WHERE key=?',(actual,int(prefix_checked),row['key']))
                if length is not None and part.stat().st_size!=length: raise SafeError('TRUNCATED_HTTP_BODY')
                info=verify(part,row['expected_size'])
                finalize(cat,row,part,info,response.status_code)
                return True
            except (AuthExpired,DiskLow,RatePaused): raise
            except SafeError as error:
                quarantine(cat,part,row['key'])
                if error.code not in ('TRANSIENT_HTTP','NETWORK_INTERRUPTED') or attempt+1 >= retries: raise
                cat.event('RETRY_TRANSIENT_REQUEST',row['key'],error.http)
                retry_wait(error,attempt,sleep)
            except OSError: raise SafeError('LOCAL_IO_FAILURE') from None
            finally:
                if response is not None: response.close()
        return False
    except (AuthExpired,DiskLow,RatePaused) as error:
        quarantine(cat,part,row['key']); cat.release(row['key'],error.code,error.http)
        if stop is not None: stop.set()
        raise
    except SafeError as error:
        quarantine(cat,part,row['key']); cat.release(row['key'],error.code,error.http,signature=cat.db.execute('SELECT signature_ok FROM manuals WHERE key=?',(row['key'],)).fetchone()[0]); return False
