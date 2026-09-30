"""Service Alliance adapter, ported from the proven legacy IGD requests."""
import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import threading
from urllib.parse import urljoin, urlparse, urlencode
import requests
from core import AuthExpired, SafeError

BASE='https://servicealliancegroup.com'
LOGIN=BASE+'/legacy/'
DASH=BASE+'/legacy/member-dashboard/'
MANUALS=DASH+'tech-manuals/'
SHORTCODE=7
# Only the existing proven APPLIANCE bootstrap is fixed. Every child is a returned object.
ROOT={'id':'1-0mICiR1DYKpV73iISmEV8DcuyTe-Pc-','name':'APPLIANCE',
      'type':'application/vnd.google-apps.folder','accountId':'11257309238295447992','pageNumber':1}


def encode_nested(value):
    pairs=[]
    def add(key,item):
        if isinstance(item,dict):
            for name,v in item.items(): add(key+'['+name+']',v)
        elif isinstance(item,list):
            for index,v in enumerate(item): add(key+'['+(str(index) if isinstance(v,(dict,list)) else '')+']',v)
        else: pairs.append((key,'' if item is None else str(item).lower() if isinstance(item,bool) else str(item)))
    for key,item in value.items(): add(key,item)
    return urlencode(pairs)


def extract_config(html):
    for script in re.findall(r'<script\b[^>]*>([\s\S]*?)</script>',html,re.I):
        match=re.search(r'\b(?:var|let|const)\s+igd\s*=\s*',script)
        if match:
            try:
                obj,_=json.JSONDecoder().raw_decode(script[match.end():].lstrip())
                if isinstance(obj.get('nonce'),str) and obj['nonce'] and obj.get('ajaxUrl'):
                    target=urljoin(BASE,obj['ajaxUrl'])
                    if urlparse(target).scheme!='https' or urlparse(target).netloc!='servicealliancegroup.com':
                        raise SafeError('IGD_ORIGIN_REJECTED')
                    return obj['nonce'],target
            except (ValueError,KeyError,TypeError): pass
    raise SafeError('CURRENT_IGD_CONFIG_NOT_FOUND')


def allowed(url):
    parsed=urlparse(url); host=parsed.hostname or ''
    return (parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443)
            and (host=='servicealliancegroup.com' or host in ('drive.google.com','drive.usercontent.google.com','docs.google.com')
                 or host.endswith('.googleusercontent.com')))


def retry_after(value):
    try: return max(0,float(value))
    except (ValueError,TypeError):
        try: return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError,OverflowError): return 0


def check_response(response):
    code=response.status_code
    if code in (401,403): raise AuthExpired()
    if code==429 or 500 <= code <= 599: raise SafeError('TRANSIENT_HTTP',code,retry_after(response.headers.get('Retry-After')))
    if code not in (200,): raise SafeError('HTTP_REQUEST_REJECTED',code)
    if '/wp-login' in response.url or '/unauthorized-access' in response.url: raise AuthExpired()

class Stream:
    def __init__(self,response):
        self.response=response; self.headers=response.headers; self.status_code=response.status_code
    def iter_content(self,size):
        try: yield from self.response.iter_content(size)
        except requests.RequestException: raise SafeError('NETWORK_INTERRUPTED') from None
    def close(self): self.response.close()

class Service:
    def __init__(self, session=None):
        self.session=session or requests.Session()
        self.session.trust_env=False
        self.session.headers.update({'User-Agent':'Titan-Windows-Manuals-Test/1.0','Accept-Encoding':'identity'})
        self.nonce=None; self.ajax=None; self.lock=threading.Lock()
    def close(self):
        self.nonce=None; self.ajax=None; self.session.cookies.clear(); self.session.close()
    def request(self,method,url,**kwargs):
        response=None
        try:
            for _ in range(9):
                if not allowed(url): raise SafeError('REDIRECT_DESTINATION_REJECTED')
                response=self.session.request(method,url,timeout=(20,90),allow_redirects=False,**kwargs)
                if response.status_code not in (301,302,303,307,308):
                    try: check_response(response)
                    except SafeError: response.close(); raise
                    return response
                target=urljoin(url,response.headers.get('Location',''))
                if not response.headers.get('Location'): response.close(); raise SafeError('REDIRECT_MISSING')
                response.close()
                # Never forward a credential POST to another host or across redirects.
                if method!='GET':
                    if response.status_code in (307,308): raise SafeError('POST_REDIRECT_REJECTED')
                    method='GET'; kwargs.pop('data',None)
                url=target
            raise SafeError('REDIRECT_LIMIT')
        except requests.RequestException:
            if response is not None: response.close()
            raise SafeError('NETWORK_INTERRUPTED') from None
    def login(self,username,password):
        with self.request('GET',LOGIN) as response: _=response.content
        body={'log':username,'pwd':password,'wp-submit':'Log In','redirect_to':DASH,
              'mepr_process_login_form':'true','mepr_is_login_page':'true'}
        with self.request('POST',LOGIN,data=body,headers={'Referer':LOGIN}) as response:
            html=response.text
            if '/legacy/member-dashboard' not in response.url or re.search(r'name=["\'](?:log|pwd)["\']',html,re.I): raise AuthExpired()
        self.refresh()
    def refresh(self):
        with self.request('GET',MANUALS,headers={'Referer':DASH}) as response:
            if '/legacy/member-dashboard/' not in response.url or re.search(r'name=["\'](?:log|pwd)["\']',response.text,re.I): raise AuthExpired()
            self.nonce,self.ajax=extract_config(response.text)
    def listing(self,folder,limit=500):
        payload={'action':'igd_get_files','shortcodeId':SHORTCODE,'data':{'folder':folder,
                 'sort':{'sortBy':'name','sortDirection':'desc'},'fileNumbers':-1,'limit':limit},'nonce':self.nonce}
        with self.request('POST',self.ajax,data=encode_nested(payload),headers={
            'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','Referer':MANUALS}) as response:
            if response.text.strip() in ('-1','0'): raise AuthExpired()
            try: envelope=response.json()
            except ValueError:
                if '<html' in response.text.lower() or 'name="pwd"' in response.text.lower(): raise AuthExpired()
                raise SafeError('METADATA_NON_JSON') from None
            if not isinstance(envelope,dict): raise SafeError('METADATA_INVALID_ENVELOPE')
            if envelope.get('success') is False: raise AuthExpired()
            data=envelope.get('data',envelope)
            if not isinstance(data,dict) or data.get('error') or not isinstance(data.get('files'),list): raise SafeError('METADATA_INVALID_FILES')
            return data
    def download(self,row):
        # Serialize session/nonce operations, allow returned streams to transfer concurrently.
        with self.lock:
            self.refresh()
            query=urlencode({'action':'igd_download','shortcodeId':SHORTCODE,'id':row['request_id'],
                             'accountId':row['account_id'],'nonce':self.nonce})
            response=self.request('GET',self.ajax+('&' if '?' in self.ajax else '?')+query,
                                  stream=True,headers={'Referer':MANUALS})
            if response.headers.get('Content-Type','').lower().startswith('text/html'):
                try:
                    head=response.raw.read(4096).lower()
                    if b'name="pwd"' in head or b'wp-login' in head or b'mepr-login' in head or b'unauthorized-access' in head: raise AuthExpired()
                    raise SafeError('HTML_RESPONSE_REJECTED',response.status_code)
                finally: response.close()
            return Stream(response)
