"""Offline synthetic PDFs and fake HTTP only. No live account or network access."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pypdf import PdfWriter
import requests
from core import *
from downloader import execute_run
from inventory import next_page, seed
from service import Service, ROOT, encode_nested, extract_config, check_response, allowed, retry_after


def pdf():
    writer=PdfWriter(); writer.add_blank_page(width=200,height=200)
    stream=io.BytesIO(); writer.write(stream); return stream.getvalue()
DATA=pdf()

class Response:
    def __init__(self,body=DATA,code=200,mime='application/pdf',length=None):
        self.body=body; self.status_code=code; self.headers={'Content-Type':mime,'Content-Length':str(len(body) if length is None else length)}
        self.url='https://drive.usercontent.google.com/download'; self.closed=False
    def iter_content(self,size): yield self.body
    def close(self): self.closed=True

class Transport:
    def __init__(self,outcomes=None): self.outcomes=list(outcomes or []); self.calls=0
    def download(self,row):
        self.calls+=1
        result=self.outcomes.pop(0) if self.outcomes else Response()
        if isinstance(result,Exception): raise result
        return result

class ListingTransport(Transport):
    def __init__(self,files=None): super().__init__(); self.files=files or []; self.list_calls=0
    def listing(self,folder,limit):
        self.list_calls+=1
        return {'files':self.files[:limit] if limit==1 else self.files,'nextPageNumber':0}

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network_block=patch('requests.sessions.Session.request',side_effect=RuntimeError('NETWORK_DISABLED_FOR_TESTS'))
        cls.network_block.start()
    @classmethod
    def tearDownClass(cls): cls.network_block.stop()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.cat=Catalog(self.temp.name)
        self.folder={'id':'folder','accountId':'account','name':'Maker','type':'application/vnd.google-apps.folder'}
        self.fkey=self.cat.add_folder(self.folder,'APPLIANCE/Maker')
    def tearDown(self): self.cat.close(); self.temp.cleanup()
    def file(self,i='file',name='manual.pdf',size=len(DATA)):
        key=self.cat.add_file({'id':str(i),'accountId':'account','name':name,'type':'application/pdf','size':size},self.fkey,'APPLIANCE/Maker',self.folder)
        return self.cat.db.execute('SELECT * FROM manuals WHERE key=?',(key,)).fetchone()
    def run_file(self,row,transport=None,**kw):
        self.cat.reserve(row['key']); return download(self.cat,row,transport or Transport(),0,sleep=lambda _:None,**kw)
    def test_sqlite_init_and_restart(self):
        self.file(); self.cat.close(); self.cat=Catalog(self.temp.name)
        self.assertEqual(self.cat.status()['discovered'],1)
    def test_success_sha_and_atomic_publication(self):
        row=self.file(); self.assertTrue(self.run_file(row)); final=Path(row['local_path'])
        result=self.cat.db.execute('SELECT * FROM manuals').fetchone()
        self.assertEqual(result['sha256'],hashlib.sha256(DATA).hexdigest()); self.assertEqual(result['actual_size'],len(DATA))
        self.assertEqual(result['verification'],'parsed_pdf'); self.assertTrue(final.exists()); self.assertFalse(Path(str(final)+'.part').exists())
    def test_success_dedup_restart(self):
        row=self.file(); self.run_file(row); self.file()
        self.assertEqual(self.cat.status()['discovered'],1); self.assertEqual(self.cat.status()['verified'],1)
    def test_duplicate_refs_separate_folders(self):
        self.file(); other={**self.folder,'id':'other'}; fk=self.cat.add_folder(other,'APPLIANCE/Other')
        self.cat.add_file({'id':'file','accountId':'account','name':'other.pdf'},fk,'APPLIANCE/Other',other)
        self.assertEqual(self.cat.status()['duplicate_references'],1); self.assertEqual(self.cat.status()['discovered'],1)
    def test_shortcut_identity(self):
        self.file(); self.cat.add_file({'id':'shortcut','accountId':'account','name':'shortcut.pdf','shortcutDetails':{'targetId':'file','targetMimeType':'application/pdf'}},self.fkey,'APPLIANCE/Maker')
        self.assertEqual(self.cat.status()['discovered'],1); self.assertEqual(self.cat.status()['duplicate_references'],1)
    def test_account_part_of_identity(self):
        self.file(); self.cat.add_file({'id':'file','accountId':'other','name':'manual.pdf'},self.fkey,'APPLIANCE/Maker')
        self.assertEqual(self.cat.status()['discovered'],2)
    def test_sanitize_reserved_invalid_and_trailing(self):
        for name in ('CON','con.pdf','AUX','LPT1.txt','COM9','NUL. '): self.assertTrue(safe_component(name).startswith('_'))
        self.assertEqual(safe_component('a<>:"/\\|?*b. '),'a_________b'); self.assertEqual(safe_component('..'),'unnamed')
    def test_filename_case_collisions_and_original(self):
        first=self.file('one','A.pdf'); second=self.file('two','a.pdf')
        self.assertNotEqual(first['local_path'].lower(),second['local_path'].lower()); self.assertEqual(first['source_filename'],'A.pdf')
    def test_filename_same_name_and_long(self):
        a=self.file('one','x'*1000+'.pdf'); b=self.file('two','x'*1000+'.pdf')
        self.assertLessEqual(len(a['local_filename']),150); self.assertNotEqual(a['local_path'],b['local_path'])
    def test_never_overwrite_existing_pdf(self):
        row=self.file(); final=Path(row['local_path']); final.parent.mkdir(); final.write_bytes(b'previous')
        self.assertFalse(self.run_file(row)); self.assertEqual(final.read_bytes(),b'previous'); self.assertEqual(self.cat.successes(),0)
    def test_html_200_rejected(self):
        row=self.file(); self.assertFalse(self.run_file(row,Transport([Response(b'<html>login</html>',mime='text/html')]))); self.assertEqual(self.cat.successes(),0)
    def test_html_as_octet_stream_rejected(self):
        row=self.file(); self.assertFalse(self.run_file(row,Transport([Response(b'<html>login</html>',mime='application/octet-stream')])));
        self.assertEqual(self.cat.db.execute('SELECT last_error FROM manuals').fetchone()[0],'NON_PDF_RESPONSE')
    def test_invalid_pdf_signature_only(self):
        row=self.file(size=None); self.assertFalse(self.run_file(row,Transport([Response(b'%PDF-1.4 fake %%EOF')])));
        self.assertEqual(self.cat.db.execute('SELECT last_error FROM manuals').fetchone()[0],'INVALID_PDF')
    def test_truncated_pdf(self):
        row=self.file(size=None); self.assertFalse(self.run_file(row,Transport([Response(DATA[:-20])])));
        self.assertEqual(self.cat.db.execute('SELECT last_error FROM manuals').fetchone()[0],'TRUNCATED_PDF')
    def test_expected_size_mismatch(self):
        row=self.file(size=100); self.assertFalse(self.run_file(row)); self.assertEqual(self.cat.successes(),0)
    def test_content_length_mismatch(self):
        row=self.file(size=None); self.assertFalse(self.run_file(row,Transport([Response(length=len(DATA)+1)])))
    def test_zero_body(self):
        row=self.file(size=None); self.assertFalse(self.run_file(row,Transport([Response(b'')])))
    def test_429_retry(self):
        row=self.file(); t=Transport([SafeError('TRANSIENT_HTTP',429,1),Response()]); waits=[]
        self.cat.reserve(row['key']); self.assertTrue(download(self.cat,row,t,0,sleep=waits.append)); self.assertEqual(t.calls,2); self.assertGreaterEqual(waits[0],1)
    def test_500_retry(self):
        row=self.file(); t=Transport([SafeError('TRANSIENT_HTTP',500),Response()]); self.assertTrue(self.run_file(row,t)); self.assertEqual(t.calls,2)
    def test_retry_exhaustion_and_resume(self):
        row=self.file(); t=Transport([SafeError('TRANSIENT_HTTP',500)]*3); self.assertFalse(self.run_file(row,t)); self.assertEqual(t.calls,3)
        self.assertTrue(self.run_file(row)); self.assertEqual(self.cat.successes(),1)
    def test_network_retry(self):
        row=self.file(); self.assertTrue(self.run_file(row,Transport([SafeError('NETWORK_INTERRUPTED'),Response()])))
    def test_auth_expiry_pauses(self):
        row=self.file(); t=Transport([AuthExpired()]); self.cat.reserve(row['key'])
        with self.assertRaises(AuthExpired): download(self.cat,row,t,0)
        self.assertEqual(t.calls,1); self.assertEqual(self.cat.successes(),0); self.assertEqual(self.cat.status()['failed'],1)
    def test_low_disk_stop_before_request(self):
        row=self.file(); t=Transport(); self.cat.reserve(row['key'])
        with patch('core.shutil.disk_usage',return_value=type('D',(),{'free':0})()):
            with self.assertRaises(DiskLow): download(self.cat,row,t,100)
        self.assertEqual(t.calls,0)
    def test_midstream_disk_stop(self):
        row=self.file(); self.cat.reserve(row['key']); checks=[None,None,DiskLow()]
        with patch('core.disk_check',side_effect=checks):
            with self.assertRaises(DiskLow): download(self.cat,row,Transport(),0)
        self.assertFalse(Path(row['local_path']).exists())
    def test_part_interrupt_recovery(self):
        row=self.file(); self.cat.reserve(row['key']); part=Path(row['local_path']+'.part'); part.parent.mkdir(); part.write_bytes(DATA[:20]); recover(self.cat)
        self.assertFalse(part.exists()); self.assertEqual(self.cat.status()['failed'],1); self.assertTrue(list((self.cat.state/'quarantine').iterdir()))
    def test_verified_staged_part_recovery(self):
        row=self.file(); self.cat.reserve(row['key']); part=Path(row['local_path']+'.part'); part.parent.mkdir(); part.write_bytes(DATA)
        with self.cat.db: self.cat.db.execute("UPDATE manuals SET verification='staged',sha256=? WHERE key=?",(hashlib.sha256(DATA).hexdigest(),row['key']))
        recover(self.cat); self.assertEqual(self.cat.successes(),1); self.assertTrue(Path(row['local_path']).exists())
    def test_crash_after_rename_recovery(self):
        row=self.file(); self.cat.reserve(row['key']); final=Path(row['local_path']); final.parent.mkdir(); final.write_bytes(DATA)
        with self.cat.db: self.cat.db.execute("UPDATE manuals SET verification='staged',sha256=? WHERE key=?",(hashlib.sha256(DATA).hexdigest(),row['key']))
        recover(self.cat); recover(self.cat); self.assertEqual(self.cat.successes(),1)
    def test_unrelated_final_during_recovery_not_adopted(self):
        row=self.file(); self.cat.reserve(row['key']); final=Path(row['local_path']); final.parent.mkdir(); final.write_bytes(DATA)
        recover(self.cat); self.assertEqual(self.cat.successes(),0); self.assertTrue(final.exists())
    def test_orphan_part_quarantine(self):
        row=self.file(); part=Path(row['local_path']+'.part'); part.parent.mkdir(); part.write_bytes(DATA); recover(self.cat)
        self.assertFalse(part.exists()); self.assertEqual(self.cat.successes(),0)
    def test_budget_sqlite_hard_limit_and_immutable_success(self):
        for i in range(10): self.run_file(self.file(i))
        with self.assertRaises(sqlite3.IntegrityError): self.cat.reserve(self.file(11)['key'])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.cat.db: self.cat.db.execute("DELETE FROM budget WHERE state='success'")
        self.assertEqual(self.cat.successes(),10)
    def test_concurrent_cap_end_to_end_and_restart(self):
        for i in range(15): self.file(i)
        t=ListingTransport(); settings={'concurrency':2,'minimum_free_gib':0,'retries':3,'delay_seconds':0,'metadata_pages_per_run':20}
        self.assertEqual(execute_run(self.cat,t,settings),'TEST_LIMIT_REACHED'); self.assertEqual(t.calls,10)
        self.assertEqual(execute_run(self.cat,t,settings),'TEST_LIMIT_REACHED'); self.assertEqual(t.calls,10)
    def test_failures_do_not_consume_success_budget(self):
        for i in range(12): self.file(i)
        t=ListingTransport(); t.outcomes=[Response(b'bad')]*2
        settings={'concurrency':1,'minimum_free_gib':0,'retries':3,'delay_seconds':0,'metadata_pages_per_run':20}
        self.assertEqual(execute_run(self.cat,t,settings),'TEST_LIMIT_REACHED'); self.assertEqual(t.calls,12)
    def test_process_lock(self):
        path=self.cat.state/'run.lock'
        with RunLock(path):
            with self.assertRaises(SafeError):
                with RunLock(path): pass
    def test_manifest_status_and_excel_safety(self):
        self.file(name='=bad.pdf'); p=self.cat.export()
        with p.open(encoding='utf-8-sig') as stream: rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['source_filename'],"'=bad.pdf"); self.assertEqual(self.cat.status()['pending'],1)
    def test_secret_filter(self):
        obj={'id':'f','name':'x','accountId':'a','nonce':'SECRET','nested':{'token':'SECRET','extra':False},'webViewLink':'SECRET'}
        self.assertNotIn('SECRET',json.dumps(clean_object(obj))); self.assertEqual(clean_object(obj)['nested'],{'extra':False})
    def test_full_folder_nested_encoding(self):
        result=parse_qs(encode_nested({'data':{'folder':{'id':'x','nested':{'a':[1,{'key':True}]}}}}))
        self.assertEqual(result['data[folder][nested][a][1][key]'],['true'])
    def test_current_nonce_not_literal(self):
        nonce,ajax=extract_config('<script>var igd = {"nonce":"DYNAMIC","ajaxUrl":"/legacy/wp-admin/admin-ajax.php"};</script>')
        self.assertEqual(nonce,'DYNAMIC'); self.assertTrue(ajax.startswith('https://servicealliancegroup.com/'))
    def test_config_origin_rejected(self):
        with self.assertRaises(SafeError): extract_config('<script>var igd = {"nonce":"x","ajaxUrl":"https://evil.test/ajax"};</script>')
    def test_redirect_allowlist(self):
        self.assertTrue(allowed('https://drive.google.com/a')); self.assertFalse(allowed('https://servicealliancegroup.com.evil.test/a')); self.assertFalse(allowed('http://drive.google.com/a'))
    def test_http_status_and_retry_after(self):
        response=Response(code=429); response.headers['Retry-After']='2'
        with self.assertRaises(SafeError) as ctx: check_response(response)
        self.assertEqual(ctx.exception.retry_after,2)
        response.status_code=403
        with self.assertRaises(AuthExpired): check_response(response)
    def test_long_retry_after_pauses_without_sleep(self):
        with self.assertRaises(SafeError) as ctx: retry_wait(SafeError('TRANSIENT_HTTP',429,600),0,lambda _:self.fail())
        self.assertEqual(ctx.exception.code,'RATE_LIMIT_PAUSED')
    def test_returned_objects_and_pagination_checkpoint(self):
        calls=[]
        class Pages:
            def listing(_,folder,limit):
                calls.append(copy.deepcopy(folder))
                if limit==1: return {'files':[]}
                if folder['id']=='folder': return {'files':[{'id':'child','accountId':'account','name':'Child','type':'application/vnd.google-apps.folder','nested':{'shared':True}}], 'nextPageNumber':0}
                return {'files':[],'nextPageNumber':0}
        next_page(self.cat,Pages(),Pacer(0)); next_page(self.cat,Pages(),Pacer(0))
        self.assertEqual(calls[-1]['nested'],{'shared':True}); self.assertEqual(self.cat.db.execute('SELECT count(*) FROM folder_pages').fetchone()[0],2)
    def test_pagination_resume(self):
        class Pages:
            def listing(_,folder,limit):
                if limit==1: return {'files':[]}
                return {'files':[{'id':str(folder['pageNumber']),'accountId':'account','name':'a.pdf'}],'nextPageNumber':2 if folder['pageNumber']==1 else 0}
        next_page(self.cat,Pages(),Pacer(0)); self.cat.close(); self.cat=Catalog(self.temp.name)
        self.assertEqual(self.cat.db.execute('SELECT page FROM folders').fetchone()[0],2)
        next_page(self.cat,Pages(),Pacer(0)); self.assertEqual(self.cat.status()['discovered'],2)
    def test_page_atomic_rollback(self):
        class Pages:
            def listing(_,folder,limit):
                return {'files':[{'id':'ok','name':'a.pdf','accountId':'account'},{'name':'b.pdf'}],'nextPageNumber':0}
        result=next_page(self.cat,Pages(),Pacer(0))
        self.assertEqual(result[0],'failed'); self.assertEqual(self.cat.status()['discovered'],0)
        self.assertEqual(self.cat.db.execute('SELECT count(*) FROM folder_pages').fetchone()[0],0)
    def test_repeated_page_rejected(self):
        class Pages:
            def listing(_,folder,limit): return {'files':[{'id':'x','accountId':'account','name':'a.pdf'}],'nextPageNumber':1}
        self.assertEqual(next_page(self.cat,Pages(),Pacer(0))[0],'failed')
    def test_auth_secrets_never_logs(self):
        row=self.file(); self.cat.reserve(row['key'])
        with self.assertRaises(AuthExpired): download(self.cat,row,Transport([AuthExpired()]),0)
        logs=(self.cat.state/'logs'/'events.jsonl').read_text(); self.assertNotIn('nonce',logs); self.assertNotIn('password',logs); self.assertNotIn('https:',logs)
    def test_no_network_suite(self):
        # Requests can never connect during these tests.
        with self.assertRaises(RuntimeError): requests.Session().request('GET','https://servicealliancegroup.com')

if __name__=='__main__':
    with patch('requests.sessions.Session.request',side_effect=RuntimeError('NETWORK_DISABLED_FOR_TESTS')):
        unittest.main(verbosity=2)
