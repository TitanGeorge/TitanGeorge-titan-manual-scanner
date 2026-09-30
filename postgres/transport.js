const BASE='https://servicealliancegroup.com';
const LOGIN=BASE+'/legacy/';
const DASH=BASE+'/legacy/member-dashboard/';
const MANUALS=BASE+'/legacy/member-dashboard/tech-manuals/';
const AJAX=BASE+'/legacy/wp-admin/admin-ajax.php';
const ACCOUNT='11257309238295447992';
const ROOT='1-0mICiR1DYKpV73iISmEV8DcuyTe-Pc-';
const BOSCH='1-J8wAAyO8e5Msbi4V2kl989z38G-Tummm';
const UA='Titan-Metadata-Inventory/1.0';
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
async function postFolder(folder,jar,nonce,limit=0){
 let target=ajaxTargets.get(jar);
 if(!target){
 const page=await request(jar,MANUALS,{method:'GET',signal:AbortSignal.timeout(90000),headers:{referer:DASH}});const html=await page.text();let ajaxUrl;
 for(const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){const match=script[1].match(/\b(?:var|let|const)\s+igd\s*=\s*(\{[\s\S]*?\})\s*;/);if(match){try{ajaxUrl=JSON.parse(match[1]).ajaxUrl}catch{}}}
 if(!ajaxUrl)throw new Error('IGD AJAX URL was not found');target=new URL(ajaxUrl,BASE);if(target.origin!==BASE)throw new Error('IGD AJAX URL origin did not match');ajaxTargets.set(jar,target);
 }
 // Preserve the entire IGD folder object; the frontend only sets pageNumber.
 const payload={action:'igd_get_files',shortcodeId:7,data:{folder,sort:{sortBy:'name',sortDirection:'desc'},fileNumbers:-1,limit},nonce};
 const body=new URLSearchParams();for(const [key,value] of Object.entries(payload))appendParams(body,key,value);
 const r=await request(jar,target.href,{method:'POST',signal:AbortSignal.timeout(90000),headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest',referer:MANUALS},body});
 if(!r.ok)throw new Error('Metadata request HTTP '+r.status);let j;try{j=JSON.parse(await r.text())}catch{throw new Error('Metadata endpoint returned non-JSON response')}
 if(j?.success===false)throw new Error('Metadata request was rejected by Service Alliance');const data=j?.data??j;
 if(data?.error)throw new Error('Service Alliance returned a metadata error');if(!Array.isArray(data?.files))throw new Error('Metadata response did not contain files[]');
 // console.log(JSON.stringify({event:'folder-metadata-response',stage:folder.id===ROOT?'APPLIANCE':'CHILD_FOLDER',httpStatus:r.status,responseType:r.headers.get('content-type')?.split(';')[0],fileCount:data.files.length,count:data.count??null,nextPageNumber:data.nextPageNumber??0}));
 return data;
}

// No side effects on import. Login and metadata calls occur only after an eligible lease.
export function createMetadataTransport(){
 let session;
 return async folder=>{
  try{session??=await loginAndGetSession();const result=await postFolder(folder,session.jar,session.nonce,500);return {files:result.files,nextPage:Number(result.nextPageNumber||0)||null};}
  catch(error){session?.jar.clear();session=undefined;throw error;}
 };
}
export {appendParams};
