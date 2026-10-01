"""Synthetic catalog promotion and mock full operation; network is forbidden."""
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Catalog, RunLock, SafeError, recover
from migration import promote, fingerprint, validate
from downloader import execute_run, main
from test_downloader import DATA, Transport, ListingTransport, Response

PROGRAM=Path(__file__).resolve().parents[1]
SETTINGS={'concurrency':1,'delay_seconds':0,'minimum_free_gib':0,'retries':3,'metadata_pages_per_run':20}

class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.cat=Catalog(self.root)
        folder={'id':'whirlpool','accountId':'synthetic','name':'Whirlpool Brands - ALL'}
        fk=self.cat.add_folder(folder,'APPLIANCE/Whirlpool Brands - ALL')
        for i in range(24):
            key=self.cat.add_file({'id':str(i),'accountId':'synthetic','name':str(i)+'.pdf','size':4325329 if i<10 else 1,'type':'application/pdf'},fk,'APPLIANCE/Wolf')
            if i<10:
                self.cat.reserve(key)
                self.cat.mark_success(key,(4325329,hashlib.sha256(('synthetic-'+str(i)).encode()).hexdigest()))
        with self.cat.db:
            self.cat.db.execute('INSERT INTO folder_items VALUES(?,?)',(fk,'synthetic:0'))
            # Previously committed different page, while the saved next page is 1.
            self.cat.db.execute('INSERT INTO folder_pages VALUES(?,?)',(fk,0))
            self.cat.db.execute('UPDATE manuals SET expected_size=? WHERE key=?',(496683330,'synthetic:10'))
        self.before=fingerprint(self.cat.db)
        self.cat.close(); self.cat=None
        self.silence=patch('builtins.print');self.silence.start()
        self.block=patch('requests.sessions.Session.request',side_effect=AssertionError('NETWORK_FORBIDDEN'));self.block.start()
    def tearDown(self):
        self.block.stop();self.silence.stop()
        if self.cat:self.cat.close()
        self.temp.cleanup()
    def open(self):self.cat=Catalog(self.root);return self.cat
    def test_exact_live_counts_and_all_values_preserved(self):
        result=promote(self.root); c=self.open(); status=c.status()
        self.assertEqual(fingerprint(c.db),self.before)
        self.assertEqual((status['discovered'],status['verified'],status['pending'],status['verified_bytes'],status['known_remaining_bytes']),(24,10,14,43253290,496683343))
        self.assertEqual(status['folder_progress'],{'source_path':'APPLIANCE/Whirlpool Brands - ALL','page':1,'status':'pending'})
        self.assertEqual(status['mode'],'full');self.assertIsNone(status['hard_limit']);self.assertFalse(status['limit_reached'])
        self.assertEqual(result['preserved_state_sha256'],self.before)
    def test_backup_readable_and_v1_usable(self):
        result=promote(self.root)
        with sqlite3.connect(result['backup']) as db:
            validate(db,'1');self.assertEqual(fingerprint(db),self.before)
            with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT INTO budget VALUES('synthetic:10','reserved','now')")
    def test_backup_failure_original_unchanged(self):
        with patch('migration.verified_backup',side_effect=SafeError('BACKUP_FAILED')):
            with self.assertRaises(SafeError):promote(self.root)
        c=self.open();self.assertEqual(c.version,1);self.assertEqual(fingerprint(c.db),self.before)
    def test_backup_verification_failure_original_unchanged(self):
        from migration import validate as real
        calls=0
        def validation(db,version):
            nonlocal calls
            calls+=1
            if calls==2:raise SafeError('UNREADABLE_BACKUP')
            return real(db,version)
        with patch('migration.validate',side_effect=validation):
            with self.assertRaises(SafeError):promote(self.root)
        self.assertEqual(self.open().version,1)
    def test_transaction_rollback_all_fault_boundaries(self):
        for stage in ('before_backup','after_backup','before_changes','after_drop','before_commit'):
            with self.subTest(stage=stage):
                def fault(current):
                    if current==stage:raise KeyboardInterrupt()
                with self.assertRaises(KeyboardInterrupt):promote(self.root,fault)
                with sqlite3.connect(self.root/'_catalog/manuals.db') as db:
                    validate(db,'1');self.assertEqual(fingerprint(db),self.before)
        promote(self.root)
    def test_process_killed_during_transaction(self):
        script="import os,sys;sys.path.insert(0,sys.argv[1]);from migration import promote;promote(sys.argv[2],lambda stage:os._exit(91) if stage=='after_drop' else None)"
        result=subprocess.run([sys.executable,'-c',script,str(PROGRAM),str(self.root)])
        self.assertEqual(result.returncode,91)
        with sqlite3.connect(self.root/'_catalog/manuals.db') as db:validate(db,'1');self.assertEqual(fingerprint(db),self.before)
        promote(self.root);self.assertEqual(self.open().version,2)
    def test_interruption_after_commit_then_idempotence(self):
        def fault(stage):
            if stage=='after_commit':raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):promote(self.root,fault)
        self.assertTrue(promote(self.root)['already_promoted'])
        self.assertEqual(len(list((self.root/'_catalog/backups').glob('*.db'))),1)
        self.assertEqual(fingerprint(self.open().db),self.before)
    def test_wal_uncheckpointed_state_backed_up(self):
        db=sqlite3.connect(self.root/'_catalog/manuals.db');db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute("UPDATE folders SET attempts=3,last_error='SYNTHETIC_HISTORY'");db.commit()
        digest=fingerprint(db);result=promote(self.root)
        with sqlite3.connect(result['backup']) as backup:self.assertEqual(fingerprint(backup),digest)
        db.close()
    def test_limit_removed_success_protection_retained(self):
        promote(self.root);c=self.open()
        for i in range(10,24):c.reserve('synthetic:'+str(i))
        self.assertEqual(c.db.execute('SELECT count(*) FROM budget').fetchone()[0],24)
        with self.assertRaises(sqlite3.IntegrityError):c.db.execute("DELETE FROM budget WHERE state='success'")
        with self.assertRaises(sqlite3.IntegrityError):c.db.execute("UPDATE budget SET state='reserved' WHERE state='success'")
    def test_unknown_schema_and_invalid_success_stop(self):
        with sqlite3.connect(self.root/'_catalog/manuals.db') as db:db.execute("UPDATE manuals SET sha256=NULL WHERE key='synthetic:0'")
        with self.assertRaises(SafeError):promote(self.root)
        self.assertFalse((self.root/'_catalog/backups').exists())
    def test_full_cli_requires_migration_before_auth(self):
        with patch('downloader.config',return_value={**SETTINGS,'destination':str(self.root)}),patch.object(sys,'argv',['downloader.py','full-resume']),patch('builtins.input',side_effect=AssertionError('AUTH_FORBIDDEN')):
            self.assertEqual(main(),5)
    def test_offline_commands_and_old_launcher_do_not_contact_source(self):
        for command in ('init','status','export','migrate','verify','resume','test'):
            with self.subTest(command=command),patch('downloader.config',return_value={**SETTINGS,'destination':str(self.root)}),patch.object(sys,'argv',['downloader.py',command]),patch('downloader.Service',side_effect=AssertionError('SERVICE_FORBIDDEN')):
                self.assertIn(main(),(0,5))
    def test_large_manifest_streaming(self):
        promote(self.root);c=self.open();row=c.db.execute('SELECT * FROM manuals LIMIT 1').fetchone()
        columns=list(row.keys())
        with c.db:
            for i in range(2000):
                values=dict(row);values.update(key='large:'+str(i),file_id='large'+str(i),local_path=str(self.root/('large'+str(i)+'.pdf')),status='pending')
                c.db.execute('INSERT INTO manuals VALUES('+','.join('?' for _ in columns)+')',[values[k] for k in columns])
        with c.export().open(encoding='utf-8-sig') as stream:self.assertEqual(sum(1 for _ in csv.DictReader(stream)),2024)
    def test_metadata_change_fails_without_replacing_verified_record(self):
        promote(self.root);c=self.open()
        with self.assertRaises(SafeError):c.add_file({'id':'0','accountId':'synthetic','name':'changed.pdf','size':4325329},'synthetic:whirlpool','APPLIANCE/Wolf')
        self.assertEqual(fingerprint(c.db),self.before)

    def test_restore_before_network_preserves_every_value(self):
        from restore_catalog import restore
        result=promote(self.root);rescue=restore(self.root,result['backup'])
        self.assertTrue(rescue.is_file());self.assertEqual(self.open().version,1)
        self.assertEqual(fingerprint(self.cat.db),self.before)
    def test_restore_rejects_loss_of_new_discovery(self):
        from restore_catalog import restore
        result=promote(self.root)
        with sqlite3.connect(self.root/'_catalog/manuals.db') as db:db.execute("UPDATE folders SET page=2")
        with self.assertRaises(SafeError):restore(self.root,result['backup'])
        self.assertEqual(self.open().version,2)
    def test_process_killed_before_and_after_commit(self):
        for stage,expected in (('after_backup','1'),('after_commit','2')):
            script="import os,sys;sys.path.insert(0,sys.argv[1]);from migration import promote;promote(sys.argv[2],lambda stage:os._exit(92) if stage==sys.argv[3] else None)"
            result=subprocess.run([sys.executable,'-c',script,str(PROGRAM),str(self.root),stage])
            self.assertEqual(result.returncode,92)
            with sqlite3.connect(self.root/'_catalog/manuals.db') as db:validate(db,expected);self.assertEqual(fingerprint(db),self.before)
        self.assertTrue(promote(self.root)['already_promoted'])
    def test_backup_write_failure_leaves_v1(self):
        with patch('migration.os.fsync',side_effect=OSError()):
            with self.assertRaises(SafeError):promote(self.root)
        self.assertEqual(self.open().version,1);self.assertEqual(fingerprint(self.cat.db),self.before)
    def test_migration_postvalidation_failure_rolls_back(self):
        from migration import validate as real
        def validation(db,version):
            if version=='2':raise SafeError('SIMULATED_VALIDATION_FAILURE')
            return real(db,version)
        with patch('migration.validate',side_effect=validation):
            with self.assertRaises(SafeError):promote(self.root)
        self.assertEqual(self.open().version,1);self.assertEqual(fingerprint(self.cat.db),self.before)

class FullOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=Catalog(self.root)
        fk=self.c.add_folder({'id':'f','accountId':'synthetic'},'APPLIANCE/Wolf')
        for i in range(26):
            key=self.c.add_file({'id':str(i),'accountId':'synthetic','name':str(i)+'.pdf','type':'application/pdf','size':len(DATA)},fk,'APPLIANCE/Wolf')
            if i<10:
                row=self.c.db.execute('SELECT * FROM manuals WHERE key=?',(key,)).fetchone();self.c.reserve(key)
                from core import download
                download(self.c,row,Transport(),0)
        with self.c.db:self.c.db.execute("UPDATE folders SET status='done'")
        self.c.close();promote(self.root);self.c=Catalog(self.root)
        self.silence=patch('builtins.print');self.silence.start()
        self.block=patch('requests.sessions.Session.request',side_effect=AssertionError('NETWORK_FORBIDDEN'));self.block.start()
    def tearDown(self):self.block.stop();self.silence.stop();self.c.close();self.temp.cleanup()
    def test_full_run_over_ten_and_resume_no_redownload(self):
        t=Transport()
        with patch('downloader.seed'),patch('downloader.next_page',return_value=None):
            self.assertEqual(execute_run(self.c,t,SETTINGS),'INVENTORY_EXHAUSTED')
            self.assertEqual(t.calls,16);self.assertEqual(self.c.status()['verified'],26)
            self.assertEqual(execute_run(self.c,t,SETTINGS),'INVENTORY_EXHAUSTED');self.assertEqual(t.calls,16)
    def test_missing_verified_files_need_review_no_redownload(self):
        row=self.c.db.execute("SELECT * FROM manuals WHERE status='verified' LIMIT 1").fetchone();Path(row['local_path']).unlink();recover(self.c)
        self.assertEqual(self.c.db.execute('SELECT status FROM manuals WHERE key=?',(row['key'],)).fetchone()[0],'needs_review')
        self.assertEqual(self.c.successes(),10)
    def test_full_resume_uses_saved_page_not_completed_page(self):
        with self.c.db:
            self.c.db.execute("UPDATE manuals SET status='needs_review' WHERE status='pending'")
            self.c.db.execute("UPDATE folders SET page=2,status='pending',warmed=1")
            self.c.db.execute("INSERT INTO folder_pages VALUES('synthetic:f',1)")
        class Pages(Transport):
            def listing(_,folder,limit):
                self.assertEqual(folder['pageNumber'],2);return {'files':[],'nextPageNumber':0}
        with patch('downloader.seed'):self.assertEqual(execute_run(self.c,Pages(),SETTINGS),'INVENTORY_EXHAUSTED')
    def test_ctrl_c_sets_stop_and_restart_recovers_reservation(self):
        signals=[]
        def interrupted(cat,row,service,threshold,retries,pacer,stop):signals.append(stop);raise KeyboardInterrupt()
        with patch('downloader.download',side_effect=interrupted),patch('downloader.seed'):
            with self.assertRaises(KeyboardInterrupt):execute_run(self.c,Transport(),SETTINGS)
        self.assertTrue(signals[0].is_set());self.assertFalse(self.c.active)
        recover(self.c);self.assertEqual(self.c.db.execute("SELECT count(*) FROM budget WHERE state='reserved'").fetchone()[0],0)

    def test_full_auth_expiration_stops_scheduling_and_resumes(self):
        from core import AuthExpired
        t=Transport([AuthExpired()])
        with patch('downloader.seed'):
            with self.assertRaises(AuthExpired):execute_run(self.c,t,SETTINGS)
        self.assertEqual(t.calls,1);self.assertEqual(self.c.successes(),10);self.assertFalse(self.c.active)
        with patch('downloader.seed'),patch('downloader.next_page',return_value=None):execute_run(self.c,t,SETTINGS)
        self.assertEqual(self.c.successes(),26)
    def test_full_429_5xx_retries_remain_bounded(self):
        t=Transport([SafeError('TRANSIENT_HTTP',429,1),SafeError('TRANSIENT_HTTP',503),Response()])
        with patch('downloader.seed'),patch('downloader.next_page',return_value=None),patch('core.retry_wait') as wait:
            execute_run(self.c,t,SETTINGS)
        self.assertEqual(wait.call_count,2);self.assertEqual(t.calls,18);self.assertEqual(self.c.successes(),26)
    def test_full_disk_stop_before_any_request(self):
        from core import DiskLow
        t=Transport()
        with patch('downloader.disk_check',side_effect=DiskLow()):
            with self.assertRaises(DiskLow):execute_run(self.c,t,SETTINGS)
        self.assertEqual(t.calls,0);self.assertEqual(self.c.successes(),10)
    def test_full_concurrency_two_keeps_completed_files(self):
        t=Transport()
        with patch('downloader.seed'),patch('downloader.next_page',return_value=None):execute_run(self.c,t,{**SETTINGS,'concurrency':2})
        self.assertEqual(t.calls,16);self.assertEqual(self.c.successes(),26)
