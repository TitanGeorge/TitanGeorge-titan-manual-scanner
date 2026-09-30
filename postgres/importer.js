import {parseCheckpoint,reconcile,ACCEPTANCE} from './checkpoint.js';
import {transaction,status,putFile} from './db.js';
export async function importCheckpoint(db,saved,{scanId,expected=ACCEPTANCE}={}){
 if(!scanId)throw new Error('scanId required');const plan=parseCheckpoint(saved,{expected});
 return transaction(db,async()=>{
  // Serialize catalog writers; workers use the same lock before page writes.
  await db.query('SELECT pg_advisory_xact_lock(741926)');
  const existing=(await db.query('SELECT * FROM scan_runs WHERE id=$1 FOR UPDATE',[scanId])).rows[0];
  if(existing){if(existing.import_sha256!==plan.sha||existing.crawling_enabled)throw new Error('Scan already exists or is enabled');const s=await status(db,scanId);reconcile({completedFolders:s.completed_folders,uniquePdfs:s.unique_pdfs,totalBytes:s.total_unique_bytes,unresolvedFolders:BigInt(s.failed_folders)+BigInt(s.pending_folders)},expected);if(Number(s.blocked_jobs)!==plan.totals.unresolvedFolders||Number(s.leased_jobs)!==0)throw new Error('Job reconciliation mismatch');return {alreadyImported:true,status:s};}
  await db.query(`INSERT INTO scan_runs(id,root_identity,account_id,status,provenance,import_sha256) VALUES($1,$2,$3,'paused','legacy_checkpoint',$4)`,[scanId,plan.root,plan.account,plan.sha]);
  const folders=new Map(),files=new Map();
  for(const f of plan.folders){const r=(await db.query(`INSERT INTO folders(scan_id,account_id,drive_folder_id,name,source_metadata,provenance,metadata_completeness,crawl_status,next_page,attempts,last_error)
   VALUES($1,$2,$3,$4,$5,'legacy_checkpoint',$6,$7,$8,$9,$10) RETURNING id`,[scanId,f.account,f.id,f.metadata?.name??null,f.metadata?JSON.stringify(f.metadata):null,f.metadata?'partial':'not_captured',f.status,f.page,f.attempts,f.error?JSON.stringify(f.error):null])).rows[0];folders.set(f.key,r.id);
   if(f.status!=='completed')await db.query(`INSERT INTO crawl_jobs(scan_id,folder_id,page,status,attempts,last_error) VALUES($1,$2,$3,'blocked',$4,$5)`,[scanId,r.id,f.page,f.attempts,JSON.stringify(f.error||{code:'legacy_pending_requires_review'})]);
  }
  for(const f of plan.files){const id=await putFile(db,f,'legacy_checkpoint');files.set(f.key,id);await db.query('INSERT INTO scan_files(scan_id,file_id) VALUES($1,$2) ON CONFLICT DO NOTHING',[scanId,id]);}
  for(const rel of plan.relationships)await db.query(`INSERT INTO folder_files(scan_id,folder_id,file_id,provenance,relationship_completeness) VALUES($1,$2,$3,'legacy_first_parent','unknown') ON CONFLICT DO NOTHING`,[scanId,folders.get(rel.folderKey),files.get(rel.fileKey)]);
  const s=await status(db,scanId);reconcile({completedFolders:s.completed_folders,uniquePdfs:s.unique_pdfs,totalBytes:s.total_unique_bytes,unresolvedFolders:BigInt(s.failed_folders)+BigInt(s.pending_folders)},expected);
  if(Number(s.blocked_jobs)!==plan.totals.unresolvedFolders||Number(s.leased_jobs)!==0)throw new Error('Job reconciliation mismatch');
  return {alreadyImported:false,status:s};
 });
}
