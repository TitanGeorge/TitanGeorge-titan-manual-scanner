"""Checkpoint each page and returned parent/child objects together."""
from contextlib import nullcontext
import json
from core import SafeError, AuthExpired, clean_object, effective_mime, now, retry_wait
from service import ROOT


def seed(cat):
    if not cat.db.execute('SELECT 1 FROM folders LIMIT 1').fetchone(): cat.add_folder(ROOT,'APPLIANCE')


def next_page(cat,service,pacer,retries=3,exclude=()):
    rows=cat.db.execute("SELECT * FROM folders WHERE status IN ('pending','failed') ORDER BY rowid").fetchall()
    job=next((row for row in rows if row['key'] not in exclude),None)
    if job is None: return None
    folder=json.loads(job['object_json']); folder['pageNumber']=job['page']
    def listing(limit):
        for attempt in range(retries):
            pacer.wait()
            try: return service.listing(folder,limit)
            except AuthExpired: raise
            except SafeError as error:
                if error.code not in ('TRANSIENT_HTTP','NETWORK_INTERRUPTED') or attempt+1>=retries: raise
                retry_wait(error,attempt)
    try:
        # Keep the proven one-item cache warmup before sorted pagination.
        if not job['warmed']:
            listing(1)
            with cat.db: cat.db.execute('UPDATE folders SET warmed=1 WHERE key=?',(job['key'],))
        data=listing(500)
        try: following=int(data.get('nextPageNumber') or 0)
        except (ValueError,TypeError): raise SafeError('METADATA_INVALID_PAGE') from None
        if following < 0: raise SafeError('METADATA_INVALID_PAGE')
        if following and (following==job['page'] or cat.db.execute('SELECT 1 FROM folder_pages WHERE folder_key=? AND page=?',(job['key'],following)).fetchone()):
            raise SafeError('METADATA_PAGINATION_LOOP')
        if following and not data['files']: raise SafeError('METADATA_PAGINATION_NO_PROGRESS')
        # Single page transaction; crash before commit leaves original page eligible.
        with cat.db:
            cat.db.execute('INSERT INTO folder_pages VALUES(?,?)',(job['key'],job['page']))
            for file in data['files']:
                if not isinstance(file,dict): raise SafeError('METADATA_INVALID_FILE')
                if not file.get('accountId'): file={**file,'accountId':folder.get('accountId')}
                mime=effective_mime(file)
                if mime=='application/vnd.google-apps.folder' or file.get('isFolder') is True:
                    cat.add_folder(file,job['source_path']+'/'+str(file.get('name') or 'unnamed'))
                elif mime=='application/pdf' or (not mime.startswith('application/vnd.google-apps.') and str(file.get('name','')).lower().endswith('.pdf')):
                    cat.add_file(file,job['key'],job['source_path'],folder)
            cat.db.execute('UPDATE folders SET page=?,status=?,attempts=attempts+1,last_error=NULL,updated=? WHERE key=?',
                           (following or job['page'],'pending' if following else 'done',now(),job['key']))
        return job['key']
    except AuthExpired: raise
    except SafeError as error:
        with cat.db: cat.db.execute("UPDATE folders SET status='failed',attempts=attempts+1,last_error=?,updated=? WHERE key=?",(error.code,now(),job['key']))
        cat.event(error.code); return ('failed',job['key'])
