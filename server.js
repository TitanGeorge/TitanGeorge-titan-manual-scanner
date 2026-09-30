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
 // Only inspect the Integrate Google Drive configuration, never another plugin's nonce.
 for(const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){
  const config=script[1].match(/\b(?:var|let|const)\s+igd\s*=\s*(\{[\s\S]*?\})\s*;/);
  if(config){try{const value=JSON.parse(config[1]).nonce;if(typeof value==='string'&&value.length)return value}catch{}}
 }
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
// Match jQuery.param's recursive form encoding used by wp.ajax.post.
function appendParams(body,key,value){
 if(Array.isArray(value)){value.forEach((item,index)=>appendParams(body,key+'['+(item!==null&&typeof item==='object'?index:'')+']',item));}
 else if(value!==null&&typeof value==='object'){for(const [name,item] of Object.entries(value))appendParams(body,key+'['+name+']',item);}
 else body.append(key,value==null?'':String(value));
}
async function postFolder(folder,jar,nonce){
 const page=await request(jar,MANUALS,{method:'GET',headers:{referer:DASH}});const html=await page.text();let ajaxUrl;
 for(const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){const match=script[1].match(/\b(?:var|let|const)\s+igd\s*=\s*(\{[\s\S]*?\})\s*;/);if(match){try{ajaxUrl=JSON.parse(match[1]).ajaxUrl}catch{}}}
 if(!ajaxUrl)throw new Error('IGD AJAX URL was not found');const target=new URL(ajaxUrl,BASE);if(target.origin!==BASE)throw new Error('IGD AJAX URL origin did not match');
 // Preserve the entire IGD folder object; the frontend only sets pageNumber.
 const payload={action:'igd_get_files',shortcodeId:7,data:{folder,sort:{sortBy:'name',sortDirection:'desc'},fileNumbers:-1,limit:0},nonce};
 const body=new URLSearchParams();for(const [key,value] of Object.entries(payload))appendParams(body,key,value);
 const r=await request(jar,target.href,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest',referer:MANUALS},body});
 if(!r.ok)throw new Error('Metadata request HTTP '+r.status);let j;try{j=JSON.parse(await r.text())}catch{throw new Error('Metadata endpoint returned non-JSON response')}
 if(j?.success===false)throw new Error('Metadata request was rejected by Service Alliance');const data=j?.data??j;
 if(data?.error)throw new Error('Service Alliance returned a metadata error');if(!Array.isArray(data?.files))throw new Error('Metadata response did not contain files[]');
 console.log(JSON.stringify({event:'folder-metadata-response',stage:folder.id===ROOT?'APPLIANCE':'BOSCH',httpStatus:r.status,responseType:r.headers.get('content-type')?.split(';')[0],fileCount:data.files.length,count:data.count??null,nextPageNumber:data.nextPageNumber??0}));
 return data;
}
async function findBosch(jar,nonce){
 const parent={id:ROOT,name:'APPLIANCE',type:'application/vnd.google-apps.folder',accountId:ACCOUNT,pageNumber:1};const seen=new Set(),pages=new Set();
 for(let i=0;i<100;i++){
  if(pages.has(parent.pageNumber))throw new Error('Metadata pagination repeated a page');pages.add(parent.pageNumber);
  const data=await postFolder(parent,jar,nonce);const matches=data.files.filter(file=>file.name==='Bosch');
  if(matches.length>1)throw new Error('APPLIANCE returned multiple Bosch entries');
  if(matches.length){const bosch=matches[0];console.log(JSON.stringify({event:'bosch-parent-entry',idMatchesExpected:bosch.id===BOSCH,accountMatchesExpected:bosch.accountId===ACCOUNT,accountType:typeof bosch.accountId,folderType:bosch.type,isFolder:bosch.isFolder,shared:bosch.shared,hasShortcutDetails:!!bosch.shortcutDetails}));return bosch;}
  for(const file of data.files)seen.add(file.id);
  const next=Number(data.nextPageNumber||0);if(!next||!data.files.length||(Number(data.count)>0&&seen.size>=Number(data.count)))break;parent.pageNumber=next;
 }
 throw new Error('APPLIANCE metadata did not contain the Bosch entry');
}
function safeError(error){
 const message=String(error?.message||'');
 const allowed=['SAG_USERNAME or SAG_PASSWORD is not configured in Render','Login did not reach authenticated member dashboard','Tech Manuals page was not authenticated','Authenticated, but igd.nonce was not found on Tech Manuals page','Metadata endpoint returned non-JSON response','Metadata request was rejected by Service Alliance','Service Alliance returned a metadata error','Metadata response did not contain files[]','Bosch metadata lookup: request rejected','Bosch metadata lookup: folder unavailable','Bosch metadata lookup: null folder metadata','Bosch metadata lookup: missing folder ID','Bosch metadata listing returned zero records','APPLIANCE returned multiple Bosch entries','APPLIANCE Bosch entry did not match the confirmed folder and account','APPLIANCE metadata did not contain the Bosch entry','Metadata pagination repeated a page','Metadata pagination exceeded test limit','IGD AJAX URL was not found','IGD AJAX URL origin did not match'];
 if(allowed.includes(message)||/^Metadata request HTTP \d{3}$/.test(message))return message;
 return 'Authentication or metadata request failed; internal details withheld';
}
app.post('/auth-test',async(req,res)=>{
 if(authTest.status==='running')return res.status(409).json({error:'auth test already running'});authTest.status='running';authTest.dashboardVerified=false;authTest.nonceFound=false;authTest.bosch=null;authTest.lastError=null;authTest.finishedAt=null;let jar;
 try{const s=await loginAndGetSession();jar=s.jar;authTest.dashboardVerified=true;authTest.nonceFound=true;const boschFolder=await findBosch(jar,s.nonce);let pageNumber=1,data;const records=new Map(),pages=new Set();do{if(pages.has(pageNumber))throw new Error('Metadata pagination repeated a page');pages.add(pageNumber);boschFolder.pageNumber=pageNumber;data=await postFolder(boschFolder,jar,s.nonce);for(const file of data.files)records.set(file.id,file);pageNumber=Number(data.nextPageNumber||0);if(!data.files.length||(Number.isFinite(Number(data.count))&&records.size>=Number(data.count)))pageNumber=0;if(pages.size>=100&&pageNumber)throw new Error('Metadata pagination exceeded test limit')}while(pageNumber);const files=[...records.values()];if(!files.length)throw new Error('Bosch metadata listing returned zero records');const folders=files.filter(x=>String(x.type||'').includes('folder')).length;const pdfs=files.filter(x=>String(x.name||'').toLowerCase().endsWith('.pdf')).length;authTest.bosch={items:files.length,folders,pdfs,count:data?.count??files.length,nextPageNumber:Number(data?.nextPageNumber||0)};authTest.status='success';authTest.finishedAt=new Date().toISOString();return res.json({success:true,mode:'BOSCH_METADATA_ONLY',authenticationSuccess:true,dashboardVerified:true,nonceFound:true,bosch:authTest.bosch,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 catch(e){authTest.status='error';authTest.lastError=safeError(e);authTest.finishedAt=new Date().toISOString();return res.status(500).json({success:false,mode:'BOSCH_METADATA_ONLY',authenticationSuccess:authTest.dashboardVerified,dashboardVerified:authTest.dashboardVerified,error:authTest.lastError,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 finally{if(jar)jar.clear()}
});

app.post('/scan',async(req,res)=>{
 if(state.status==='running')return res.status(409).json({error:'scan already running'});return res.status(400).json({error:'Full scan is intentionally disabled until the Bosch authentication test succeeds.'});
});
app.listen(PORT,()=>console.log(`Titan metadata-only scanner listening on ${PORT}`));

