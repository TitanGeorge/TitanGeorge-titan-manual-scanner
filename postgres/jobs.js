import {transaction,putFile} from './db.js';
import {digest,metadata,observedIdentity,identity,size} from './checkpoint.js';
const leaseSeconds=value=>{if(!Number.isInteger(value)||value<1||value>3600)throw new Error('Invalid lease duration');return value;};
export async function claimJob(db,scanId,owner,seconds=60){
 if(typeof owner!=='string'||!owner)throw new Error('Lease owner required');leaseSeconds(seconds);
 return transaction(db,async()=>{
  await db.query('SELECT pg_advisory_xact_lock(741926)');
  const r=(await db.query('SELECT crawling_enabled FROM scan_runs WHERE id=$1 FOR UPDATE',[scanId])).rows[0];if(!r?.crawling_enabled)return null;
  const job=(await db.query(`SELECT j.* FROM crawl_jobs j JOIN folders f ON f.id=j.folder_id
   WHERE j.scan_id=$1 AND f.crawl_status<>'completed' AND f.next_page=j.page AND
   ((j.status IN ('pending','retry') AND j.available_after<=now()) OR (j.status='processing' AND j.lease_expires_at<=now()))
   ORDER BY j.available_after,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1`,[scanId])).rows[0];if(!job)return null;
  const leased=(await db.query(`UPDATE crawl_jobs SET status='processing',lease_owner=$2,lease_token=lease_token+1,
   lease_expires_at=now()+($3::integer * interval '1 second'),attempts=attempts+1,updated_at=now() WHERE id=$1 RETURNING *`,[job.id,owner,seconds])).rows[0];
  await db.query("UPDATE folders SET crawl_status='processing',attempts=attempts+1,updated_at=now() WHERE id=$1",[job.folder_id]);
  await db.query('UPDATE scan_runs SET updated_at=now() WHERE id=$1',[scanId]);return leased;
 });
}
async function fenced(db,lease,{completed=false}={}){
 const run=(await db.query('SELECT crawling_enabled FROM scan_runs WHERE id=$1 FOR UPDATE',[lease.scan_id])).rows[0];
 const job=(await db.query('SELECT * FROM crawl_jobs WHERE id=$1 AND scan_id=$2 FOR UPDATE',[lease.id,lease.scan_id])).rows[0];
 if(!job||String(job.lease_token)!==String(lease.lease_token)||job.folder_id!==lease.folder_id||job.page!==lease.page)throw new Error('Stale worker');
 if(completed&&job.status==='completed')return job;
 if(!run?.crawling_enabled||job.status!=='processing'||job.lease_owner!==lease.lease_owner||new Date(job.lease_expires_at)<=new Date())throw new Error('Stale worker or disabled scan');
 // Database clock remains authoritative even if application clock differs.
 if(!(await db.query('SELECT lease_expires_at>clock_timestamp() AS valid FROM crawl_jobs WHERE id=$1',[job.id])).rows[0].valid)throw new Error('Expired lease');return job;
}
export async function heartbeat(db,lease,seconds=60){leaseSeconds(seconds);return transaction(db,async()=>{await db.query('SELECT pg_advisory_xact_lock(741926)');await fenced(db,lease);return (await db.query("UPDATE crawl_jobs SET lease_expires_at=clock_timestamp()+($2::integer * interval '1 second'),updated_at=now() WHERE id=$1 RETURNING *",[lease.id,seconds])).rows[0];});}
export async function failJob(db,lease,{retryAfterSeconds=60,retryable=true}={}){
 if(!Number.isInteger(retryAfterSeconds)||retryAfterSeconds<0)throw new Error('Invalid retry delay');
 return transaction(db,async()=>{await db.query('SELECT pg_advisory_xact_lock(741926)');await fenced(db,lease);
  await db.query(`UPDATE crawl_jobs SET status=$2,lease_owner=NULL,lease_expires_at=NULL,available_after=now()+($3::integer*interval '1 second'),last_error=$4,updated_at=now() WHERE id=$1`,[lease.id,retryable?'retry':'blocked',retryAfterSeconds,JSON.stringify({code:retryable?'metadata_page_retry':'metadata_page_blocked'})]);
  await db.query("UPDATE folders SET crawl_status=$2,last_error=$3,updated_at=now() WHERE id=$1",[lease.folder_id,retryable?'pending':'failed',JSON.stringify({code:'metadata_page_failure'})]);await db.query('UPDATE scan_runs SET updated_at=now() WHERE id=$1',[lease.scan_id]);
 });
}
// Consumes already-observed metadata. This module has no source request or crawler loop.
export async function processPage(db,lease,{files,nextPage=null}){
 if(!Array.isArray(files)||files.some(f=>!f||typeof f!=='object'||typeof f.id!=='string'||!f.id))throw new Error('Invalid page');
 if(nextPage!==null&&(!Number.isSafeInteger(nextPage)||nextPage<1||nextPage===Number(lease.page)))throw new Error('Invalid next page');
 const clean=files.map(metadata),hash=digest({files:clean,nextPage});
 return transaction(db,async()=>{
  await db.query('SELECT pg_advisory_xact_lock(741926)');const job=await fenced(db,lease,{completed:true});
  if(job.status==='completed'){const old=(await db.query('SELECT result_sha256 FROM processed_pages WHERE job_id=$1',[job.id])).rows[0];if(old?.result_sha256!==hash)throw new Error('Page replay differs');return {alreadyCommitted:true};}
  const folder=(await db.query('SELECT * FROM folders WHERE id=$1 FOR UPDATE',[job.folder_id])).rows[0];if(folder.crawl_status==='completed'||Number(folder.next_page)!==Number(job.page))throw new Error('Completed folder or pagination mismatch');
  for(const source of clean){
   const key=observedIdentity(source,folder.account_id),{account,id}=identity(key),type=source.shortcutDetails?.targetMimeType||source.type||source.mimeType||'';
   if(type==='application/vnd.google-apps.folder'||source.isFolder===true){
    const child=(await db.query(`INSERT INTO folders(scan_id,account_id,drive_folder_id,parent_identity,name,source_metadata,provenance,metadata_completeness,crawl_status,next_page,discovered_at)
     VALUES($1,$2,$3,$4,$5,$6,'direct_igd','observed','pending',1,now()) ON CONFLICT(scan_id,account_id,drive_folder_id) DO UPDATE SET
     name=coalesce(excluded.name,folders.name),source_metadata=excluded.source_metadata,provenance='direct_igd',metadata_completeness='observed',updated_at=now() RETURNING *`,[job.scan_id,account,id,`${folder.account_id}:${folder.drive_folder_id}`,source.name??null,JSON.stringify(source)])).rows[0];
    if(child.crawl_status!=='completed')await db.query(`INSERT INTO crawl_jobs(scan_id,folder_id,page,status) VALUES($1,$2,$3,'pending') ON CONFLICT DO NOTHING`,[job.scan_id,child.id,child.next_page||1]);
   }else if(type==='application/pdf'||(!type.includes('google-apps')&&/\.pdf$/i.test(source.name||''))){
    const fid=await putFile(db,{account,id,name:source.name??null,size:size(source.size),mime:type||'application/pdf',metadata:source},'direct_igd');
    await db.query('INSERT INTO scan_files(scan_id,file_id) VALUES($1,$2) ON CONFLICT DO NOTHING',[job.scan_id,fid]);
    await db.query(`INSERT INTO folder_files(scan_id,folder_id,file_id,provenance,relationship_completeness) VALUES($1,$2,$3,'direct_igd','observed_partial') ON CONFLICT DO NOTHING`,[job.scan_id,job.folder_id,fid]);
   }
  }
  if(nextPage!==null){
   if((await db.query('SELECT id FROM crawl_jobs WHERE scan_id=$1 AND folder_id=$2 AND page=$3',[job.scan_id,job.folder_id,nextPage])).rows.length)throw new Error('Pagination repeated a page');
   await db.query(`INSERT INTO crawl_jobs(scan_id,folder_id,page,status) VALUES($1,$2,$3,'pending')`,[job.scan_id,job.folder_id,nextPage]);
  }
  await db.query(`UPDATE folders SET crawl_status=$2,next_page=$3,completed_at=CASE WHEN $3::bigint IS NULL THEN now() ELSE completed_at END,last_error=NULL,updated_at=now() WHERE id=$1`,[job.folder_id,nextPage===null?'completed':'pending',nextPage]);
  await db.query("UPDATE crawl_jobs SET status='completed',lease_owner=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=now() WHERE id=$1",[job.id]);
  await db.query('INSERT INTO processed_pages(job_id,result_sha256) VALUES($1,$2)',[job.id,hash]);
  // Check the original lease again immediately before commit, using DB wall clock.
  if(!(await db.query('SELECT $1::timestamptz>clock_timestamp() AS valid',[job.lease_expires_at])).rows[0].valid)throw new Error('Lease expired before page commit');
  await db.query(`UPDATE scan_runs SET updated_at=now(),status=CASE WHEN EXISTS(SELECT 1 FROM crawl_jobs WHERE scan_id=$1 AND status<>'completed') THEN 'running' ELSE 'completed' END WHERE id=$1`,[job.scan_id]);
  return {alreadyCommitted:false};
 });
}
