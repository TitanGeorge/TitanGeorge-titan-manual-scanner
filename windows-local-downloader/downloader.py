"""Windows entry point. All real download commands share the permanent ten-file budget."""
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

from core import AuthExpired, Catalog, DiskLow, HARD_LIMIT, Pacer, RunLock, SafeError, disk_check, download, recover
from inventory import next_page, seed
from service import Service


def config():
    try:
        obj=json.loads(Path(__file__).with_name('config.json').read_text())
        required={'destination','concurrency','delay_seconds','minimum_free_gib','retries','metadata_pages_per_run'}
        if set(obj)!=required: raise ValueError()
        if not 1 <= obj['concurrency'] <= 2 or not isinstance(obj['concurrency'],int): raise ValueError()
        if not 1 <= obj['delay_seconds'] <= 60: raise ValueError()
        if not 1 <= obj['minimum_free_gib'] <= 10000: raise ValueError()
        if not isinstance(obj['retries'],int) or not 1 <= obj['retries'] <= 5: raise ValueError()
        if not isinstance(obj['metadata_pages_per_run'],int) or not 1 <= obj['metadata_pages_per_run'] <= 20: raise ValueError()
        root=Path(obj['destination'])
        if not root.is_absolute() or len(str(root))>65: raise ValueError()
        if os.name=='nt' and str(root).startswith('\\\\'): raise ValueError()
        return obj
    except (OSError,ValueError,TypeError): raise SafeError('CONFIG_INVALID') from None


def execute_run(cat,service,settings):
    """Downloads interleaved with bounded discovery; never pre-crawls the full library."""
    recover(cat)
    if cat.successes() >= HARD_LIMIT:
        cat.event('TEST_LIMIT_REACHED'); return 'TEST_LIMIT_REACHED'
    threshold=int(settings['minimum_free_gib']*1024**3)
    disk_check(cat.root,threshold)
    seed(cat)
    failed_folders=set(); attempted=set(); pages=0
    pacer=Pacer(settings['delay_seconds']); stop=threading.Event()
    def worker(row):
        local=Catalog(cat.root)
        try: return download(local,row,service,threshold,settings['retries'],pacer,stop=stop)
        finally: local.close()
    with ThreadPoolExecutor(max_workers=settings['concurrency']) as pool:
        while cat.successes() < HARD_LIMIT:
            rows=[row for row in cat.db.execute("SELECT * FROM manuals WHERE status IN ('pending','failed') ORDER BY discovered,key").fetchall() if row['key'] not in attempted]
            if rows:
                available=HARD_LIMIT-cat.db.execute('SELECT count(*) FROM budget').fetchone()[0]
                batch=rows[:min(settings['concurrency'],available)]
                if not batch: return 'BUDGET_RESERVATIONS_PAUSED'
                futures=[]
                for row in batch:
                    cat.reserve(row['key']); attempted.add(row['key']); futures.append(pool.submit(worker,row))
                failure=None
                for future in futures:
                    try: future.result()
                    except (AuthExpired,DiskLow) as error: stop.set(); failure=error
                    except BaseException: stop.set(); raise
                if failure: raise failure
            else:
                if pages >= settings['metadata_pages_per_run']: return 'METADATA_PAGE_LIMIT_PAUSED_RESUME'
                disk_check(cat.root,threshold)
                result=next_page(cat,service,pacer,settings['retries'],failed_folders)
                if result is None: return 'PENDING_ERRORS_RESUME' if failed_folders or any(cat.status()[k] for k in ('failed','pending')) else 'INVENTORY_EXHAUSTED'
                pages+=1
                if isinstance(result,tuple): failed_folders.add(result[1])
    cat.event('TEST_LIMIT_REACHED')
    return 'TEST_LIMIT_REACHED'


def print_status(cat):
    result=cat.status()
    print(json.dumps(result,indent=2))
    path=cat.state/'reports'/'status.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')


def main():
    command=sys.argv[1] if len(sys.argv)==2 else ''
    if command not in ('test','resume','status','export','init'):
        print('Usage: downloader.py init | test | resume | status | export'); return 2
    cat=None; service=None
    try:
        settings=config(); root=Path(settings['destination'])
        root.mkdir(parents=True,exist_ok=True)
        with RunLock(root/'_catalog'/'run.lock'):
            cat=Catalog(root)
            if command=='status': print_status(cat); return 0
            if command=='export': print('Manifest:',cat.export()); return 0
            if command=='init':
                disk_check(root,int(settings['minimum_free_gib']*1024**3)); print('SQLite initialized. No network requests made.'); return 0
            recover(cat)
            if cat.successes() >= HARD_LIMIT:
                print('TEST LIMIT REACHED: 10 verified PDFs. No further downloads permitted.'); print_status(cat); return 0
            disk_check(root,int(settings['minimum_free_gib']*1024**3))
            print('TEST MODE: permanent maximum of 10 successful PDFs across test and resume runs.')
            print('Credentials are entered locally and remain in memory only.')
            username=input('Service Alliance username: ')
            password=getpass.getpass('Service Alliance password: ')
            service=Service()
            try: service.login(username,password)
            finally: username=password=None
            result=execute_run(cat,service,settings)
            print(result); print_status(cat); cat.export()
            return 0
    except AuthExpired:
        print('Authentication expired or login rejected. Run resume-download.bat and log in again.'); return 3
    except DiskLow:
        print('Paused for low disk space. Free space, then run resume-download.bat.'); return 4
    except SafeError as error: print(error.code); return 5
    except KeyboardInterrupt: print('Interrupted safely. Resume will reconcile partial files.'); return 130
    except (OSError,sqlite3.Error): print('LOCAL_STORAGE_ERROR. Check drive permissions and available space.'); return 6
    except Exception:
        # No raw exception text, requests URLs, remote HTML, or authentication material.
        print('UNEXPECTED_FAILURE_DETAILS_WITHHELD. Share status.json and events.jsonl for review.'); return 7
    finally:
        if service: service.close()
        if cat:
            try: print_status(cat); cat.export()
            except Exception: pass
            cat.close()

if __name__=='__main__': sys.exit(main())
