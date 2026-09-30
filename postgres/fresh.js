import {randomUUID} from 'node:crypto';
import {transaction} from './db.js';
import {folderMetadata,observedIdentity} from './checkpoint.js';
export async function initializeFresh(db,{scanId=randomUUID(),root,allowParallel=false}={}){
 const clean=folderMetadata(root);if(!clean.id||!clean.accountId)throw new Error('Root ID and account required');
 return transaction(db,async()=>{
  await db.query('SELECT pg_advisory_xact_lock(741926)');
  if(!allowParallel&&(await db.query("SELECT id FROM scan_runs WHERE account_id=$1 AND root_identity=$2 AND status NOT IN ('completed','cancelled')",[String(clean.accountId),observedIdentity(clean,clean.accountId)])).rows.length)throw new Error('An active scan already exists for root');
  await db.query("INSERT INTO scan_runs(id,root_identity,account_id,provenance,allow_parallel_scan) VALUES($1,$2,$3,'fresh_igd',$4)",[scanId,observedIdentity(clean,clean.accountId),String(clean.accountId),allowParallel]);
  const f=(await db.query("INSERT INTO folders(scan_id,account_id,drive_folder_id,name,source_metadata,provenance,metadata_completeness,crawl_status,next_page) VALUES($1,$2,$3,$4,$5,'fresh_root','observed','pending',1) RETURNING id",[scanId,String(clean.accountId),clean.id,clean.name??null,JSON.stringify(clean)])).rows[0];
  await db.query("INSERT INTO crawl_jobs(scan_id,folder_id,page,status) VALUES($1,$2,1,'pending')",[scanId,f.id]);return scanId;
 });
}
export async function setPaused(db,scanId,paused){
 if(typeof paused!=='boolean')throw new Error('Explicit pause boolean required');
 return transaction(db,async()=>{await db.query('SELECT pg_advisory_xact_lock(741926)');if(!paused)await db.query('UPDATE scan_runs SET page_budget=NULL WHERE id=$1',[scanId]);const r=await db.query("UPDATE scan_runs SET crawling_enabled=$2,status=CASE WHEN $2 THEN 'running' ELSE 'paused' END,updated_at=now() WHERE id=$1 AND status NOT IN ('completed','cancelled') RETURNING id",[scanId,!paused]);if(!r.rows.length)throw new Error('Scan missing or terminal');});
}

export async function enableValidation(db,scanId){
 return transaction(db,async()=>{await db.query('SELECT pg_advisory_xact_lock(741926)');const r=await db.query("UPDATE scan_runs SET page_budget=1+(SELECT count(*) FROM processed_pages p JOIN crawl_jobs j ON j.id=p.job_id WHERE j.scan_id=$1),max_concurrency=1,crawling_enabled=true,status='running',updated_at=now() WHERE id=$1 AND NOT crawling_enabled AND status NOT IN ('completed','cancelled') AND NOT EXISTS(SELECT 1 FROM crawl_jobs WHERE scan_id=$1 AND status='processing' AND lease_expires_at>clock_timestamp()) RETURNING id",[scanId]);if(!r.rows.length)throw new Error('Scan missing or terminal');});
}
