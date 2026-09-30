import express from 'express';
import fs from 'node:fs/promises';
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
const ajaxTargets=new WeakMap();
async function postFolder(folder,jar,nonce){
 let target=ajaxTargets.get(jar);
 if(!target){
 const page=await request(jar,MANUALS,{method:'GET',signal:AbortSignal.timeout(90000),headers:{referer:DASH}});const html=await page.text();let ajaxUrl;
 for(const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){const match=script[1].match(/\b(?:var|let|const)\s+igd\s*=\s*(\{[\s\S]*?\})\s*;/);if(match){try{ajaxUrl=JSON.parse(match[1]).ajaxUrl}catch{}}}
 if(!ajaxUrl)throw new Error('IGD AJAX URL was not found');target=new URL(ajaxUrl,BASE);if(target.origin!==BASE)throw new Error('IGD AJAX URL origin did not match');ajaxTargets.set(jar,target);
 }
 // Preserve the entire IGD folder object; the frontend only sets pageNumber.
 const payload={action:'igd_get_files',shortcodeId:7,data:{folder,sort:{sortBy:'name',sortDirection:'desc'},fileNumbers:-1,limit:0},nonce};
 const body=new URLSearchParams();for(const [key,value] of Object.entries(payload))appendParams(body,key,value);
 const r=await request(jar,target.href,{method:'POST',signal:AbortSignal.timeout(90000),headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest',referer:MANUALS},body});
 if(!r.ok)throw new Error('Metadata request HTTP '+r.status);let j;try{j=JSON.parse(await r.text())}catch{throw new Error('Metadata endpoint returned non-JSON response')}
 if(j?.success===false)throw new Error('Metadata request was rejected by Service Alliance');const data=j?.data??j;
 if(data?.error)throw new Error('Service Alliance returned a metadata error');if(!Array.isArray(data?.files))throw new Error('Metadata response did not contain files[]');
 console.log(JSON.stringify({event:'folder-metadata-response',stage:folder.id===ROOT?'APPLIANCE':'CHILD_FOLDER',httpStatus:r.status,responseType:r.headers.get('content-type')?.split(';')[0],fileCount:data.files.length,count:data.count??null,nextPageNumber:data.nextPageNumber??0}));
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
 const allowed=['SAG_USERNAME or SAG_PASSWORD is not configured in Render','Login did not reach authenticated member dashboard','Tech Manuals page was not authenticated','Authenticated, but igd.nonce was not found on Tech Manuals page','Metadata endpoint returned non-JSON response','Metadata request was rejected by Service Alliance','Service Alliance returned a metadata error','Metadata response did not contain files[]','Bosch metadata lookup: request rejected','Bosch metadata lookup: folder unavailable','Bosch metadata lookup: null folder metadata','Bosch metadata lookup: missing folder ID','Bosch metadata listing returned zero records','APPLIANCE returned multiple Bosch entries','APPLIANCE Bosch entry did not match the confirmed folder and account','APPLIANCE metadata did not contain the Bosch entry','Metadata pagination repeated a page','Metadata pagination exceeded test limit','IGD AJAX URL was not found','IGD AJAX URL origin did not match','Metadata pagination made no progress','Inventory interrupted','Metadata checkpoint could not be loaded'];
 if(allowed.includes(message)||/^Metadata request HTTP \d{3}$/.test(message))return message;
 return 'Authentication or metadata request failed; internal details withheld';
}
app.post('/auth-test',async(req,res)=>{
 if(state.status==='running'||state.status==='resolving_sizes')return res.status(409).json({error:'APPLIANCE inventory is running'});if(authTest.status==='running')return res.status(409).json({error:'auth test already running'});authTest.status='running';authTest.dashboardVerified=false;authTest.nonceFound=false;authTest.bosch=null;authTest.lastError=null;authTest.finishedAt=null;let jar;
 try{const s=await loginAndGetSession();jar=s.jar;authTest.dashboardVerified=true;authTest.nonceFound=true;const boschFolder=await findBosch(jar,s.nonce);let pageNumber=1,data;const records=new Map(),pages=new Set();do{if(pages.has(pageNumber))throw new Error('Metadata pagination repeated a page');pages.add(pageNumber);boschFolder.pageNumber=pageNumber;data=await postFolder(boschFolder,jar,s.nonce);for(const file of data.files)records.set(file.id,file);pageNumber=Number(data.nextPageNumber||0);if(!data.files.length||(Number.isFinite(Number(data.count))&&records.size>=Number(data.count)))pageNumber=0;if(pages.size>=100&&pageNumber)throw new Error('Metadata pagination exceeded test limit')}while(pageNumber);const files=[...records.values()];if(!files.length)throw new Error('Bosch metadata listing returned zero records');const folders=files.filter(x=>String(x.type||'').includes('folder')).length;const pdfs=files.filter(x=>String(x.name||'').toLowerCase().endsWith('.pdf')).length;authTest.bosch={items:files.length,folders,pdfs,count:data?.count??files.length,nextPageNumber:Number(data?.nextPageNumber||0)};authTest.status='success';authTest.finishedAt=new Date().toISOString();return res.json({success:true,mode:'BOSCH_METADATA_ONLY',authenticationSuccess:true,dashboardVerified:true,nonceFound:true,bosch:authTest.bosch,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 catch(e){authTest.status='error';authTest.lastError=safeError(e);authTest.finishedAt=new Date().toISOString();return res.status(500).json({success:false,mode:'BOSCH_METADATA_ONLY',authenticationSuccess:authTest.dashboardVerified,dashboardVerified:authTest.dashboardVerified,error:authTest.lastError,pdfDownloads:0,pdfBytesStored:0,secretsLogged:false});}
 finally{if(jar)jar.clear()}
});

const SCAN_DIR=process.env.SCAN_STATE_DIR||'/tmp/titan-manual-inventory';
const CHECKPOINT=SCAN_DIR+'/checkpoint.json';
const scanQueue=[];const folderSeen=new Set(),folderDone=new Set(),pdfRecords=new Map(),failedJobs=[];
let runner=null,scanSession=null,sessionAt=0,stopping=false,checkpointWrites=Promise.resolve();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const folderMime='application/vnd.google-apps.folder';
const effectiveType=file=>file.shortcutDetails?.targetMimeType||file.type||file.mimeType||'';
const objectKey=file=>(file.accountId||ACCOUNT)+':'+(file.shortcutDetails?.targetId||file.id);
function numericSize(value){if(value===null||value===undefined||value==='')return null;const s=String(value);return /^\d+$/.test(s)?s:null;}
function checkpointFolder(value){
 if(Array.isArray(value))return value.map(checkpointFolder);
 if(value&&typeof value==='object'){const clean={};for(const [key,item] of Object.entries(value)){if(/nonce|cookie|password|credential|authorization|session|token|secret|resource.?key|Link|exportLinks/i.test(key))continue;clean[key]=checkpointFolder(item)}return clean;}
 return value;
}
function syncTotals(){
 let bytes=0n,missing=0;for(const file of pdfRecords.values()){if(file.size===null)missing++;else bytes+=BigInt(file.size)}
 Object.assign(state,{folders:folderDone.size,foldersDiscovered:folderSeen.size,queuedFolders:scanQueue.length,pdfs:pdfRecords.size,bytes:bytes<=BigInt(Number.MAX_SAFE_INTEGER)?Number(bytes):bytes.toString(),totalStorageBytes:bytes.toString(),totalStorageGB:Number(bytes)/1e9,totalStorageGiB:Number(bytes)/1073741824,missingSize:missing,failedFolders:failedJobs.length,storageTotalComplete:missing===0&&failedJobs.length===0&&state.status==='completed',pdfDownloads:0,pdfBytesStored:0,checkpointStorage:process.env.SCAN_STATE_DIR?'configured-directory':'ephemeral',updatedAt:new Date().toISOString()});
}
function saveCheckpoint(){
 syncTotals();const snapshot=JSON.stringify({version:1,root:ROOT,account:ACCOUNT,state,queue:scanQueue.map(job=>({...job,folder:checkpointFolder(job.folder)})),seen:[...folderSeen],done:[...folderDone],pdfs:[...pdfRecords],failed:failedJobs.map(job=>({...job,folder:checkpointFolder(job.folder)}))});
 checkpointWrites=checkpointWrites.catch(()=>{}).then(async()=>{await fs.mkdir(SCAN_DIR,{recursive:true,mode:0o700});await fs.writeFile(CHECKPOINT+'.tmp',snapshot,{mode:0o600});await fs.rename(CHECKPOINT+'.tmp',CHECKPOINT)});return checkpointWrites;
}
async function loadCheckpoint(){
 try{const saved=JSON.parse(await fs.readFile(CHECKPOINT,'utf8'));if(saved.version!==1||saved.root!==ROOT||saved.account!==ACCOUNT)throw new Error('Invalid metadata checkpoint');Object.assign(state,saved.state);scanQueue.push(...saved.queue);saved.seen.forEach(key=>folderSeen.add(key));saved.done.forEach(key=>folderDone.add(key));saved.pdfs.forEach(([key,file])=>pdfRecords.set(key,file));failedJobs.push(...saved.failed);syncTotals();return true}
 catch(error){if(error.code==='ENOENT')return false;throw new Error('Metadata checkpoint could not be loaded')}
}
function enqueueFolder(folder){if(!folder?.id)return;const key=objectKey(folder);if(folderSeen.has(key))return;folderSeen.add(key);scanQueue.push({folder,key,round:0});}
async function getScanSession(force=false){
 if(force||!scanSession||Date.now()-sessionAt>30*60*1000){if(scanSession)scanSession.jar.clear();scanSession=await loginAndGetSession();sessionAt=Date.now();state.sessionRenewals=(state.sessionRenewals||0)+1}
 return scanSession;
}
async function listWithRetry(folder){
 let last;for(let attempt=0;attempt<5;attempt++){
  if(stopping)throw new Error('Inventory interrupted');
  try{const session=await getScanSession(attempt>0&&attempt%2===1);return await postFolder(folder,session.jar,session.nonce)}
  catch(error){last=error;state.retries=(state.retries||0)+1;state.lastError=safeError(error);await saveCheckpoint();if(attempt<4)await delay(Math.min(30000,1500*2**attempt))}
 }
 throw last;
}
function recordPdf(file,parent){
 const key=objectKey(file),existing=pdfRecords.get(key),size=numericSize(file.size);
 if(existing){if(existing.size===null&&size!==null)existing.size=size;return;}
 pdfRecords.set(key,{id:file.shortcutDetails?.targetId||file.id,name:file.name,accountId:file.accountId||parent.accountId||ACCOUNT,mimeType:effectiveType(file)||'application/pdf',size,parentId:parent.shortcutDetails?.targetId||parent.id,shortcutId:file.shortcutDetails?.targetId?file.id:null});
}
async function scanOne(job){
 const seenFiles=new Set(),pages=new Set();let pageNumber=1;
 for(let i=0;i<10000;i++){
  if(pages.has(pageNumber))throw new Error('Metadata pagination repeated a page');pages.add(pageNumber);
  job.folder.pageNumber=pageNumber;const data=await listWithRetry(job.folder);state.requests=(state.requests||0)+1;
  let fresh=0;for(const file of data.files){const key=objectKey(file);if(!seenFiles.has(key)){seenFiles.add(key);fresh++}if(effectiveType(file)===folderMime||file.isFolder===true)enqueueFolder(file);else if(effectiveType(file)==='application/pdf'||(!effectiveType(file).includes('google-apps')&&/\.pdf$/i.test(file.name||'')))recordPdf(file,job.folder)}
  const next=Number(data.nextPageNumber||0),count=Number(data.count);
  if(!next||!data.files.length||(Number.isFinite(count)&&seenFiles.size>=count))return;
  if(!fresh)throw new Error('Metadata pagination made no progress');
  pageNumber=next;await saveCheckpoint();await delay(250);
 }
 throw new Error('Metadata pagination exceeded test limit');
}
async function resolveMissingSizes(){
 for(const file of pdfRecords.values()){
  if(stopping)return;if(file.size!==null)continue;
  try{const session=await getScanSession();const body=new URLSearchParams({action:'igd_get_file',shortcodeId:'7',id:file.id,accountId:file.accountId,nonce:session.nonce});const r=await request(session.jar,AJAX,{method:'POST',signal:AbortSignal.timeout(90000),headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest',referer:MANUALS},body});if(r.ok){const envelope=await r.json();if(envelope.success!==false&&envelope.data?.id===file.id)file.size=numericSize(envelope.data.size)}}catch{state.sizeLookupFailures=(state.sizeLookupFailures||0)+1}
  await saveCheckpoint();await delay(250);
 }
}
async function inventoryLoop(){
 try{
  state.status='running';state.startedAt=state.startedAt||new Date().toISOString();state.finishedAt=null;await saveCheckpoint();
  while(scanQueue.length&&!stopping){
   const job=scanQueue[0];state.currentFolder=job.folder.name||null;
   try{await scanOne(job);folderDone.add(job.key);scanQueue.shift();state.lastError=null}
   catch(error){if(stopping)break;scanQueue.shift();state.lastError=safeError(error);if(job.round<2){job.round++;scanQueue.push(job)}else failedJobs.push({...job,error:safeError(error)})}
   await saveCheckpoint();await delay(250);
  }
  if(!stopping){state.status='resolving_sizes';await saveCheckpoint();await resolveMissingSizes();state.status=failedJobs.length?'completed_with_errors':'completed';state.finishedAt=new Date().toISOString();state.currentFolder=null;await saveCheckpoint()}
 }catch(error){state.status='error';state.lastError=safeError(error);await saveCheckpoint().catch(()=>{})}
 finally{if(scanSession)scanSession.jar.clear();scanSession=null;runner=null;}
}
function startInventory(){
 if(runner)return false;if(state.status==='completed')return false;
 if(!scanQueue.length&&!folderSeen.size)enqueueFolder({id:ROOT,name:'APPLIANCE',type:folderMime,accountId:ACCOUNT,pageNumber:1});
 if(!scanQueue.length&&failedJobs.length){while(failedJobs.length){const job=failedJobs.shift();job.round=0;scanQueue.push(job)}}
 runner=inventoryLoop();return true;
}
app.post('/scan',(req,res)=>{if(authTest.status==='running')return res.status(409).json({error:'Bosch test is running'});const started=startInventory();res.status(started?202:200).json({started,mode:'APPLIANCE_METADATA_ONLY',status:state.status,pdfDownloads:0,pdfBytesStored:0});});
app.get('/health',(req,res)=>res.json({ok:true}));
process.on('SIGTERM',()=>{stopping=true;saveCheckpoint().finally(()=>process.exit(0));});
const restored=await loadCheckpoint();syncTotals();
app.listen(PORT,()=>{console.log('Titan APPLIANCE metadata inventory listening');if(!restored||!['completed'].includes(state.status))startInventory();});
