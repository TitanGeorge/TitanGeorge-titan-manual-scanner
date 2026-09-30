import {status,transaction} from './db.js';
import {ACCEPTANCE} from './checkpoint.js';
// Aggregate history alone cannot identify old individual files or folders.
export async function comparisonReport(db,scanId,{baselineScanId=null}={}){
 return transaction(db,async()=>{
  await db.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
  const current=await status(db,scanId);if(!current)throw new Error('Unknown scan');
  const unresolved=(await db.query("SELECT account_id,drive_folder_id,name,crawl_status FROM folders WHERE scan_id=$1 AND crawl_status<>'completed' ORDER BY id",[scanId])).rows;
  const relationships=(await db.query('SELECT count(*) AS relationships,count(DISTINCT file_id) AS referenced_files FROM folder_files WHERE scan_id=$1',[scanId])).rows[0];
  const report={scanId,historicalBenchmark:{...ACCEPTANCE,unresolvedFolder:'UNSORTED MISC PDF FILES'},current,delta:{completedFolders:Number(current.completed_folders)-ACCEPTANCE.completedFolders,uniquePdfs:Number(current.unique_pdfs)-ACCEPTANCE.uniquePdfs,totalBytes:(BigInt(current.total_unique_bytes)-BigInt(ACCEPTANCE.totalBytes)).toString()},unresolvedFolders:unresolved,relationships:{total:Number(relationships.relationships),extraFolderReferences:Number(relationships.relationships)-Number(relationships.referenced_files),duplicateIdenticalRows:0},interpretation:'Differences may be legitimate library changes. No automatic correction or rescan.',detailAvailability:'Unavailable against lost checkpoint: aggregate totals cannot reveal individual changes.',details:null};
  if(!baselineScanId)return report;
  if(!await status(db,baselineScanId))throw new Error('Unknown baseline scan');
  const snapshots=async id=>(await db.query(`SELECT f.account_id,f.drive_file_id,CASE WHEN sf.observed_metadata IS NOT NULL THEN sf.observed_name ELSE f.filename END AS filename,CASE WHEN sf.observed_metadata IS NOT NULL THEN sf.observed_size ELSE f.size_bytes END AS size_bytes FROM scan_files sf JOIN files f ON f.id=sf.file_id WHERE sf.scan_id=$1`,[id])).rows;
  const map=rows=>new Map(rows.map(f=>[JSON.stringify([f.account_id,f.drive_file_id]),f]));const before=map(await snapshots(baselineScanId)),after=map(await snapshots(scanId));
  const newPdfs=[],noLongerObserved=[],changedFilenames=[],changedSizes=[];
  for(const [key,file] of after){const old=before.get(key);if(!old)newPdfs.push(file);else{if(old.filename!==file.filename)changedFilenames.push({identity:key,before:old.filename,after:file.filename});if(String(old.size_bytes)!==String(file.size_bytes))changedSizes.push({identity:key,before:old.size_bytes,after:file.size_bytes});}}
  for(const [key,file] of before)if(!after.has(key))noLongerObserved.push(file);
  const folderMap=async id=>map((await db.query('SELECT account_id,drive_folder_id AS drive_file_id,name FROM folders WHERE scan_id=$1',[id])).rows);const oldFolders=await folderMap(baselineScanId),newFolders=await folderMap(scanId);
  report.details={baselineScanId,newPdfs,noLongerObserved,changedFilenames,changedSizes,newFolders:[...newFolders].filter(([k])=>!oldFolders.has(k)).map(([,v])=>v),missingFolders:[...oldFolders].filter(([k])=>!newFolders.has(k)).map(([,v])=>v)};
  report.detailAvailability='Available against retained baseline scan snapshots; absence means not observed, not proven deletion.';return report;
 });
}
