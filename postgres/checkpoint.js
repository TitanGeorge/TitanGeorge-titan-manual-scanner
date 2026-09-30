import {createHash} from 'node:crypto';
export const ACCEPTANCE=Object.freeze({completedFolders:217,uniquePdfs:92266,totalBytes:'146394392927',unresolvedFolders:1});
export const digest=value=>createHash('sha256').update(typeof value==='string'?value:JSON.stringify(value)).digest('hex');
const fail=message=>{throw new Error(message)};
export function identity(key){
 if(typeof key!=='string'||!key.includes(':'))fail('Invalid source identity');
 const i=key.indexOf(':');const account=key.slice(0,i),id=key.slice(i+1);
 if(!account||!id)fail('Empty source identity');return {account,id};
}
export function size(value){
 if(value===null||value===undefined)return null;
 if(!/^\d+$/.test(String(value)))fail('Invalid size');
 const n=BigInt(value);if(n>9223372036854775807n)fail('Size exceeds PostgreSQL bigint');return n.toString();
}
// Deliberately allowlist metadata rather than trying to remove every possible secret.
export function metadata(source){
 const result={};for(const k of ['id','name','accountId','parentId','shortcutId','type','mimeType','size','pageNumber','isFolder']){
  const v=source?.[k];if(v!==undefined&&v!==null&&['string','number','boolean'].includes(typeof v))result[k]=v;
 }
 if(source?.shortcutDetails){result.shortcutDetails={};for(const k of ['targetId','targetMimeType'])if(typeof source.shortcutDetails[k]==='string')result.shortcutDetails[k]=source.shortcutDetails[k];}
 return result;
}
export function observedIdentity(source,fallback){return `${source.accountId||fallback}:${source.shortcutDetails?.targetId||source.id}`;}
export function reconcile(actual,expected=ACCEPTANCE){
 for(const k of Object.keys(ACCEPTANCE))if(String(actual[k])!==String(expected[k]))fail(`Reconciliation mismatch: ${k}; expected ${expected[k]}, got ${actual[k]}`);
}
export function parseCheckpoint(saved,{expected=ACCEPTANCE}={}){
 if(saved?.version!==1||typeof saved.root!=='string'||!saved.root||!['string','number'].includes(typeof saved.account)||!saved.state||typeof saved.state!=='object')fail('Invalid checkpoint header');
 for(const k of ['seen','done','queue','failed','pdfs'])if(!Array.isArray(saved[k]))fail(`Invalid checkpoint ${k}`);
 const seen=new Set(saved.seen),done=new Set(saved.done);
 if(seen.size!==saved.seen.length||done.size!==saved.done.length)fail('Duplicate folder identities');
 const folders=new Map();for(const key of seen){const {account,id}=identity(key);folders.set(key,{key,account,id,status:done.has(key)?'completed':'pending',metadata:null,page:null,attempts:0});}
 for(const key of done)if(!seen.has(key))fail('Completed folder absent from seen');
 const unresolved=new Set();
 for(const [list,status] of [[saved.queue,'pending'],[saved.failed,'failed']])for(const job of list){
  if(!job?.folder||typeof job.key!=='string'||!seen.has(job.key)||done.has(job.key)||unresolved.has(job.key))fail('Inconsistent unresolved job');
  if(observedIdentity(job.folder,saved.account)!==job.key)fail('Folder object identity mismatch');
  const page=Number(job.folder.pageNumber||1);if(!Number.isSafeInteger(page)||page<1)fail('Invalid pagination');
  if(job.round!==undefined&&(!Number.isSafeInteger(job.round)||job.round<0))fail('Invalid attempts');
  const f=folders.get(job.key);Object.assign(f,{status,metadata:metadata(job.folder),page,attempts:job.round||0,error:status==='failed'?{code:'legacy_folder_failure',...(typeof job.error==='string'&&/HTTP 500/.test(job.error)?{httpStatus:500}:{})}:null});unresolved.add(job.key);
 }
 for(const key of seen)if(!done.has(key)&&!unresolved.has(key))fail('Discovered folder has no retained job');
 const files=new Map();let bytes=0n,missing=0;const relationships=[];let unmatchedParents=0,accountDifferences=0;
 for(const entry of saved.pdfs){
  if(!Array.isArray(entry)||entry.length!==2||!entry[1]||typeof entry[1]!=='object')fail('Invalid PDF record');
  const [key,file]=entry,{account,id}=identity(key);if(files.has(key))fail('Duplicate PDF identity in checkpoint');
  if(String(file.id)!==id)fail('Stored PDF identity differs from key');
  if(String(file.accountId)!==account)accountDifferences++;
  const n=size(file.size);if(n===null)missing++;else bytes+=BigInt(n);
  files.set(key,{key,account,id,name:file.name??null,size:n,mime:file.mimeType??null,metadata:metadata(file)});
  // Only link a retained parent ID when it resolves unambiguously in the retained folder identities.
  if(file.parentId){const candidates=[...folders.values()].filter(f=>f.id===String(file.parentId));if(candidates.length===1)relationships.push({folderKey:candidates[0].key,fileKey:key});else unmatchedParents++;}
 }
 const totals={completedFolders:done.size,uniquePdfs:files.size,totalBytes:bytes.toString(),unresolvedFolders:unresolved.size};reconcile(totals,expected);
 if(expected===ACCEPTANCE){const f=[...folders.values()].filter(f=>f.status!=='completed');if(f.length!==1||f[0].status!=='failed'||f[0].metadata?.name!=='UNSORTED MISC PDF FILES'||saved.queue.length)fail('Expected only the blocked UNSORTED MISC PDF FILES folder');}
 return {root:saved.root,account:String(saved.account),folders:[...folders.values()],files:[...files.values()],relationships,totals,sha:digest(saved),report:{...totals,missingSizePdfs:missing,knownRelationships:relationships.length,unmatchedParents,accountDifferences,folderInserts:folders.size,fileUpserts:files.size,relationshipInserts:relationships.length,blockedJobs:unresolved.size,databaseChanges:false,existingDatabaseCompared:false}};
}
