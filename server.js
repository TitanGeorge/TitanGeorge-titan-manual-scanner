import express from 'express';
const app=express();
app.use(express.json());
const PORT=process.env.PORT||10000;
const BASE='https://servicealliancegroup.com';
const LOGIN=BASE+'/legacy/';
const DASH=BASE+'/legacy/member-dashboard/';
const MANUALS=BASE+'/legacy/member-dashboard/tech-manuals/';
const AJAX=BASE+'/legacy/wp-admin/admin-ajax.php';
const ACCOUNT='11257309238295447992';
const ROOT='1-0mICiR1DYKpV73iISmEV8DcuyTe-Pc-';
const BOSCH='1-J8wAAyO8e5Msbi4V2kl989z38G-Tummm';
const UA='Titan-Metadata-Inventory/1.0';
const state={status:'idle',folders:0,pdfs:0,bytes:0,missingSize:0,failedFolders:0,lastError:null,startedAt:null,finishedAt:null};
const authTest={status:'not-run',dashboardVerified:false,nonceFound:false,bosch:null,lastError:null,finishedAt:null};
app.get('/',(req,res)=>res.json({name:'Titan Manual Scanner',mode:'METADATA_ONLY',downloads:false,persistentPdfStorage:false,state,authTest}));
app.get('/status',(req,res)=>res.json(state));
app.get('/auth-test/status',(req,res)=>res.json(authTest));

class CookieJar{
 constructor(){this.map=new Map()}
 add(headers){for(const s of getSetCookies(headers)){const first=s.split(';',1)[0],i=first.indexOf('=');if(i>0){const k=first.slice(0,i).trim(),v=first.slice(i+1);if(/max-age=0|expires=thu, 01 jan 1970/i.test(s))this.map.delete(k);else this.map.set(k,v)}}}
 header(){return [...this.map].map(([k,v])=>`${k}=${v}`).join('; ')}
 clear(){this.map.clear()}
}
function getSetCookies(headers){if(typeof headers.getSetCookie==='function')return headers.getSetCookie();const v=headers.get('set-cookie');return v?[v]:[]}
async function request(jar,url,opts={}){
 const headers=new Headers(opts.headers||{});headers.set('user-agent',UA);headers.set('accept','text/html,application/json;q=0.9,*/*;q=0.8');const c=jar.header();if(c)headers.set('cookie',c);
 const r=await fetch(url,{...opts,headers,redirect:'manual'});jar.add(r.headers);return r;
}
async function follow(jar,r,max=8){let cur=r;for(let i=0;i<max&&cur.status>=300&&cur.status<400;i++){const loc=cur.headers.get('location');if(!loc)break;cur=await request(jar,new URL(loc,cur.url).href,{method:'GET'})}return cur}
function extractNonce(html){
 const patterns=[/\bnonce\s*:\s*["']([^"']+)["']/i,/\bnonce["']?\s*:\s*["']([^"']+)["']/i,/["']nonce["']\s*:\s*["']([^"']+)["']/i];
 for(const p of patterns){const m=html.match(p);if(m)return m[1]}
 return null;
}
function dashboardLooksAuthenticated(url,html){return /\/legacy\/member-dashboard\/?/i.test(url)&&!/unauthorized-access|name=["']log["']|name=["']pwd["']/i.test(html)}
async function loginAndGetSession(){
 const username=process.env.SAG_USERNAME,password=process.env.SAG_PASSWORD;if(!username||!password)throw new Error('SAG_USERNAME or SAG_PASSWORD is not configured in Render');
 const jar=new CookieJar();let r=await request(jar,LOGIN,{method:'GET'});await r.text();
 const body=new URLSearchParams({log:username,pwd:password,rememberme:'forever','wp-submit':'Log In',redirect_to:DASH,mepr_process_login_form:'true',mepr_is_login_page:'true'});
 r=await request(jar,LOGIN,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','referer':LOGIN},body});r=await follow(jar,r);let html=await r.text();
 if(!dashboardLooksAuthenticated(r.url,html)){jar.clear();throw new Error('Login did not reach authenticated member dashboard')}
 r=await request(jar,MANUALS,{method:'GET',headers:{referer:DASH}});r=await follow(jar,r);html=await r.text();
 if(!dashboardLooksAuthenticated(r.url,html)){jar.clear();throw new Error('Tech Manuals page was not authenticated')}
 const nonce=extractNonce(html);if(!nonce){jar.clear();throw new Error('Authenticated, but igd.nonce was not found on Tech Manuals page')}
 return {jar,nonce};
}
async function postFolder(folder,jar,nonce){
 const body=new URLSearchParams();body.set('action','igd_get_files');body.set('shortcodeId','7');body.set('nonce',nonce);body.set('data[folder][id]',folder.id);body.set('data[folder][name]',folder.name||'');body.set('data[folder][accountId]',folder.accountId||ACCOUNT);body.set('data[folder][pageNumber]',String(folder.pageNumber||1));body.set('data[sort][sortBy]','name');body.set('data[sort][sortDirection]','asc');body.set('data[fileNumbers]','-1');body.set('data[limit]','0');
 const r=await request(jar,AJAX,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest','referer':MANUALS},body});if(!r.ok)throw new Error('Metadata request HTTP '+r.status);const text=await r.text();let j;try{j=JSON.parse(text)}catch{throw new Error('Metadata endpoint returned non-JSON response')}return j?.data??j;
}
app.post('/auth-test',async(req,res)=>{
 if(authTest.status==='running')return res.status(409).json({error:'auth test already running'});authTest.status='running';authTest.dashboardVerified=false;authTest.nonceFound=false;authTest.bosch=null;authTest.lastError=null;authTest.finishedAt=null;let jar;
 try{const s=await loginAndGetSession();jar=s.jar;authTest.dashboardVerified=true;authTest.nonceFound=true;const data=await postFolder({id:BOSCH,name:'Bosch',accountId:ACCOUNT,pageNumber:1},jar,s.nonce);const files=Array.isArray(data?.files)?data.files:[];const folders=files.filter(x=>String(x.type||'').includes('folder')).length;const pdfs=files.filter(x=>String(x.name||'').toLowerCase().endsWith('.pdf')).length;authTest.bosch={items:files.length,folders,pdfs,count:data?.count??files.length,nextPageNumber:Number(data?.nextPageNumber||0)};authTest.status='success';authTest.finishedAt=new Date().toISOString();return res.json({success:true,mode:'BOSCH_METADATA_ONLY',dashboardVerified:true,nonceFound:true,bosch:authTest.bosch,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 catch(e){authTest.status='error';authTest.lastError=String(e?.message||e);authTest.finishedAt=new Date().toISOString();return res.status(500).json({success:false,mode:'BOSCH_METADATA_ONLY',error:authTest.lastError,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 finally{if(jar)jar.clear()}
});

app.post('/scan',async(req,res)=>{
 if(state.status==='running')return res.status(409).json({error:'scan already running'});return res.status(400).json({error:'Full scan is intentionally disabled until the Bosch authentication test succeeds.'});
});
app.listen(PORT,()=>console.log(`Titan metadata-only scanner listening on ${PORT}`));
