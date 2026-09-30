"""Fake HTTP sessions verify protocol fields without accessing the service."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
from core import Catalog, AuthExpired, SafeError, Pacer, recover, download
from inventory import next_page
from service import Service, MANUALS, DASH

class HTTP:
    def __init__(self,url,body='',code=200,headers=None):
        self.url=url; self.text=body; self.content=body.encode(); self.raw=io.BytesIO(self.content)
        self.status_code=code; self.headers=headers or {}; self.closed=False
    def json(self): return json.loads(self.text)
    def close(self): self.closed=True
    def __enter__(self): return self
    def __exit__(self,*a): self.close()
    def iter_content(self,size): yield self.content

class FakeSession:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]; self.headers={}; self.cookies={}; self.closed=False
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs)); return self.responses.pop(0)
    def close(self): self.closed=True

CONFIG='<script>var igd = {"nonce":"SESSION_ONLY_SENTINEL","ajaxUrl":"/legacy/wp-admin/admin-ajax.php"};</script>'

class ServiceTests(unittest.TestCase):
    def test_login_fields_current_config_and_clearing(self):
        fake=FakeSession([HTTP('https://servicealliancegroup.com/legacy/'),HTTP(DASH),HTTP(MANUALS,CONFIG)])
        service=Service(fake); service.login('USERNAME_SENTINEL','PASSWORD_SENTINEL')
        body=fake.calls[1][2]['data']; self.assertEqual(body['log'],'USERNAME_SENTINEL'); self.assertEqual(body['pwd'],'PASSWORD_SENTINEL')
        self.assertEqual(body['redirect_to'],DASH); self.assertEqual(service.nonce,'SESSION_ONLY_SENTINEL')
        service.close(); self.assertIsNone(service.nonce); self.assertTrue(fake.closed)
    def test_metadata_fields_and_full_folder(self):
        fake=FakeSession([HTTP(MANUALS,json.dumps({'success':True,'data':{'files':[],'nextPageNumber':0}}))])
        service=Service(fake); service.nonce='SESSION_ONLY_SENTINEL'; service.ajax='https://servicealliancegroup.com/legacy/wp-admin/admin-ajax.php'
        service.listing({'id':'returned','accountId':'account','nested':{'flag':True},'pageNumber':2})
        body=parse_qs(fake.calls[0][2]['data']); self.assertEqual(body['action'],['igd_get_files']); self.assertEqual(body['shortcodeId'],['7'])
        self.assertEqual(body['data[folder][nested][flag]'],['true']); self.assertEqual(body['data[folder][pageNumber]'],['2'])
    def test_download_request_redirect_and_stream(self):
        ajax='https://servicealliancegroup.com/legacy/wp-admin/admin-ajax.php'
        drive='https://drive.usercontent.google.com/download?id=fixture'
        fake=FakeSession([HTTP(MANUALS,CONFIG),HTTP(ajax,code=302,headers={'Location':drive}),HTTP(drive,'%PDF-fixture',headers={'Content-Type':'application/pdf'})])
        service=Service(fake); result=service.download({'request_id':'file','account_id':'account'})
        params=parse_qs(urlparse(fake.calls[1][1]).query)
        self.assertEqual(params,{'action':['igd_download'],'shortcodeId':['7'],'id':['file'],'accountId':['account'],'nonce':['SESSION_ONLY_SENTINEL']})
        self.assertTrue(fake.calls[-1][2]['stream']); self.assertEqual(b''.join(result.iter_content(1024)),b'%PDF-fixture'); result.close()
    def test_login_html_pause_no_retained_html(self):
        ajax='https://servicealliancegroup.com/legacy/wp-admin/admin-ajax.php'
        fake=FakeSession([HTTP(MANUALS,CONFIG),HTTP(ajax,'<html><input name="pwd">SECRET</html>',headers={'Content-Type':'text/html'})])
        with self.assertRaises(AuthExpired): Service(fake).download({'request_id':'file','account_id':'account'})
        self.assertTrue(fake.responses==[])
    def test_external_post_redirect_never_forwards_credentials(self):
        fake=FakeSession([HTTP('https://servicealliancegroup.com/legacy/',code=307,headers={'Location':'https://drive.google.com/a'})])
        with self.assertRaises(SafeError): Service(fake).request('POST','https://servicealliancegroup.com/legacy/',data={'pwd':'SECRET'})
        self.assertEqual(len(fake.calls),1)
    def test_blocked_redirect_before_request(self):
        fake=FakeSession([HTTP('https://servicealliancegroup.com/a',code=302,headers={'Location':'https://evil.example/x'})])
        with self.assertRaises(SafeError): Service(fake).request('GET','https://servicealliancegroup.com/a')
        self.assertEqual(len(fake.calls),1)
    def test_metadata_nonce_rejection(self):
        fake=FakeSession([HTTP(MANUALS,'-1')]); service=Service(fake); service.ajax=MANUALS; service.nonce='x'
        with self.assertRaises(AuthExpired): service.listing({'id':'f','accountId':'a'})
    def test_network_errors_sanitized(self):
        class Broken(FakeSession):
            def request(self,*a,**kw): raise requests.ConnectionError('https://secret.example/?nonce=SECRET')
        with self.assertRaises(SafeError) as ctx: Service(Broken([])).request('GET',MANUALS)
        self.assertEqual(str(ctx.exception),'NETWORK_INTERRUPTED')
    def test_stream_interruption_sanitized(self):
        from service import Stream
        class Broken(HTTP):
            def iter_content(self,size): raise requests.ConnectionError('SECRET')
        with self.assertRaises(SafeError) as ctx: list(Stream(Broken(MANUALS)).iter_content(100))
        self.assertNotIn('SECRET',str(ctx.exception))
    def test_long_rate_limit_stops_run(self):
        from core import RatePaused
        from downloader import execute_run
        from test_downloader import ListingTransport
        with tempfile.TemporaryDirectory() as root:
            cat=Catalog(root); fk=cat.add_folder({'id':'f','accountId':'a'},'APPLIANCE')
            for i in range(12): cat.add_file({'id':str(i),'accountId':'a','name':'x.pdf'},fk,'APPLIANCE')
            t=ListingTransport(); t.outcomes=[SafeError('TRANSIENT_HTTP',429,600)]
            settings={'concurrency':1,'minimum_free_gib':0,'retries':3,'delay_seconds':0,'metadata_pages_per_run':20}
            with self.assertRaises(RatePaused): execute_run(cat,t,settings)
            self.assertEqual(t.calls,1); self.assertEqual(cat.successes(),0); cat.close()
    def test_metadata_no_progress_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            cat=Catalog(root); cat.add_folder({'id':'f','accountId':'a'},'APPLIANCE')
            class Pages:
                def listing(self,folder,limit): return {'files':[{'id':'same','accountId':'a','name':'x.pdf'}],'nextPageNumber':folder['pageNumber']+1}
            next_page(cat,Pages(),Pacer(0)); result=next_page(cat,Pages(),Pacer(0))
            self.assertEqual(result[0],'failed'); self.assertEqual(cat.db.execute('SELECT page FROM folders').fetchone()[0],2); cat.close()
    def test_deleted_verified_does_not_reset_cap(self):
        from core import verify,finalize
        with tempfile.TemporaryDirectory() as root:
            from test_downloader import DATA, Transport
            cat=Catalog(root); fk=cat.add_folder({'id':'f','accountId':'a'},'APPLIANCE')
            key=cat.add_file({'id':'file','accountId':'a','name':'x.pdf'},fk,'APPLIANCE'); row=cat.db.execute('SELECT * FROM manuals').fetchone()
            cat.reserve(key); self.assertTrue(download(cat,row,Transport(),0)); Path(row['local_path']).unlink(); recover(cat)
            self.assertEqual(cat.successes(),1); self.assertEqual(cat.status()['needs_review'],1); cat.close()
    def test_disguised_login_never_quarantined(self):
        from test_downloader import Transport,Response
        with tempfile.TemporaryDirectory() as root:
            cat=Catalog(root); fk=cat.add_folder({'id':'f','accountId':'a'},'APPLIANCE'); key=cat.add_file({'id':'file','accountId':'a','name':'x.pdf'},fk,'APPLIANCE')
            row=cat.db.execute('SELECT * FROM manuals').fetchone(); cat.reserve(key)
            with self.assertRaises(AuthExpired): download(cat,row,Transport([Response(b'<html>name="pwd" SECRET</html>',mime='application/octet-stream')]),0)
            self.assertEqual(list((cat.state/'quarantine').iterdir()),[]); self.assertFalse(Path(row['local_path']+'.part').exists()); cat.close()

if __name__=='__main__': unittest.main(verbosity=2)
