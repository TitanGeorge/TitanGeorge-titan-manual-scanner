"""Windows entry point. Full operation requires an explicit full-resume command after offline promotion."""
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

from core import AuthExpired, Catalog, DiskLow, HARD_LIMIT, RatePaused, Pacer, RunLock, SafeError, disk_check, download, recover
from inventory import next_page, seed
from service import Service


def config():
    try:
        obj=json.loads(Path(__file__).with_name('config.json').read_text())
        required={'destination','concurrency','delay_seconds','minimum_free_gib','retries','metadata_pages_per_run'}
        if set(obj)!=required: raise ValueError()
        if not 1 <= obj['concurrency'] <= 2 or not isinstance(obj['concurrency'],int): raise ValueError()
        if not 1 <= obj['delay_seconds'] <= 60: raise ValueError()
        if not 5 <= obj['minimum_free_gib'] <= 10000: raise ValueError()
        if not isinstance(obj['retries'],int) or not 1 <= obj['retries'] <= 5: raise ValueError()
        if not isinstance(obj['metadata_pages_per_run'],int) or not 1 <= obj['metadata_pages_per_run'] <= 20: raise ValueError()
        root=Path(obj['destination'])
        if not root.is_absolute() or len(str(root).encode('utf-16-le'))//2>65: raise ValueError()
        if os.name=='nt' and str(root).startswith('\\\\'): raise ValueError()
        return obj
    except (OSError,ValueError,TypeError): raise SafeError('CONFIG_INVALID') from None


def execute_run(cat,service,settings):
    """Downloads interleaved with bounded discovery; never pre-crawls the full library."""
    recover(cat)
    if cat.hard_limit is not None and cat.successes() >= cat.hard_limit:
        cat.event('TEST_LIMIT_REACHED'); return 'TEST_LIMIT_REACHED'
    threshold=int(settings['minimum_free_gib']*1024**3)
    disk_check(cat.root,threshold)
    seed(cat)
    failed_folders=set(); pages=0
    cat.db.execute('CREATE TEMP TABLE IF NOT EXISTS attempted(key TEXT PRIMARY KEY)')
    cat.db.execute('DELETE FROM attempted'); cat.db.commit()
    pacer=Pacer(settings['delay_seconds']); stop=threading.Event()
    def worker(row):
        local=Catalog(cat.root)
        try: return download(local,row,service,threshold,settings['retries'],pacer,stop=stop)
        finally: local.close()
    cat.active=True
    try:
      with ThreadPoolExecutor(max_workers=settings['concurrency']) as pool:
        try:
            while cat.hard_limit is None or cat.successes() < cat.hard_limit:
                rows=cat.db.execute("SELECT m.* FROM manuals m WHERE status IN ('pending','failed') AND NOT EXISTS (SELECT 1 FROM attempted a WHERE a.key=m.key) ORDER BY discovered,key LIMIT ?",(settings['concurrency'],)).fetchall()
                if rows:
                    available=settings['concurrency'] if cat.hard_limit is None else cat.hard_limit-cat.db.execute('SELECT count(*) FROM budget').fetchone()[0]
                    batch=rows[:min(settings['concurrency'],available)]
                    if not batch: return 'BUDGET_RESERVATIONS_PAUSED'
                    futures=[]
                    for row in batch:
                        cat.reserve(row['key'])
                        with cat.db: cat.db.execute('INSERT INTO attempted VALUES(?)',(row['key'],))
                        futures.append(pool.submit(worker,row))
                    failure=None
                    for future in futures:
                        try: future.result()
                        except (AuthExpired,DiskLow,RatePaused) as error: stop.set(); failure=error
                        except BaseException: stop.set(); raise
                    if failure: raise failure
                    print_status(cat)
                else:
                    if cat.hard_limit is not None and pages >= settings['metadata_pages_per_run']: return 'METADATA_PAGE_LIMIT_PAUSED_RESUME'
                    disk_check(cat.root,threshold)
                    result=next_page(cat,service,pacer,settings['retries'],failed_folders)
                    if result is None: return 'PENDING_ERRORS_RESUME' if failed_folders or any(cat.status()[k] for k in ('failed','pending')) else 'INVENTORY_EXHAUSTED'
                    pages+=1
                    print_status(cat)
                    if isinstance(result,tuple): failed_folders.add(result[1])
        except BaseException:
            stop.set()
            raise
    finally:
        cat.active=False
    cat.event('TEST_LIMIT_REACHED')
    return 'TEST_LIMIT_REACHED'


def print_status(cat):
    result=cat.status()
    print(json.dumps(result,indent=2))
    path=cat.state/'reports'/'status.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')


def main():
    command=sys.argv[1] if len(sys.argv)==2 else ''
    if command not in ('test','resume','full-resume','migrate','verify','status','export','init'):
        print('Usage: downloader.py init | migrate | verify | status | export | full-resume'); return 2
    cat=None; service=None; lock=None
    try:
        settings=config(); root=Path(settings['destination'])
        root.mkdir(parents=True,exist_ok=True)
        lock=RunLock(root/'_catalog'/'run.lock'); lock.__enter__()
        if command=='migrate':
            from migration import promote
            print(json.dumps(promote(root),indent=2)); return 0
        cat=Catalog(root)
        if command=='status': print_status(cat); return 0
        if command=='export': print('Manifest:',cat.export()); return 0
        if command=='init':
            disk_check(root,int(settings['minimum_free_gib']*1024**3)); print('SQLite initialized. No network requests made.'); return 0
        if command=='verify':
            recover(cat); print_status(cat); return 0
        if command in ('test','resume'):
            raise SafeError('FULL_MODE_REQUIRES_EXPLICIT_FULL_RESUME')
        if command=='full-resume' and cat.version!=2:
            raise SafeError('OFFLINE_MIGRATION_REQUIRED')
        recover(cat)
        if cat.hard_limit is not None and cat.successes() >= cat.hard_limit:
            print('TEST LIMIT REACHED: 10 verified PDFs. No further downloads permitted.'); print_status(cat); return 0
        disk_check(root,int(settings['minimum_free_gib']*1024**3))
        print('FULL MODE: explicit start/resume, no PDF ceiling.' if cat.version==2 else 'TEST MODE: permanent maximum of 10 successes.')
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
    except RatePaused:
        print('Server rate limit requires a pause. Wait before using resume-download.bat.'); return 8
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
        if lock and lock.handle: lock.__exit__()

if __name__=='__main__': sys.exit(main())
