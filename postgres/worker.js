import {claimJob,processPage,failJob} from './jobs.js';
// One dedicated PostgreSQL connection per worker. No pool.query transaction sharing.
export async function workOne(db,scanId,owner,fetchMetadata,{leaseSeconds=600}={}){
 const lease=await claimJob(db,scanId,owner,leaseSeconds);if(!lease)return {claimed:false};
 try{
  const folder=(await db.query('SELECT source_metadata FROM folders WHERE id=$1',[lease.folder_id])).rows[0]?.source_metadata;
  if(!folder?.id)throw new Error('Missing retained folder');
  // Recheck pause after claim and immediately before invoking the authenticated transport.
  if(!(await db.query('SELECT crawling_enabled FROM scan_runs WHERE id=$1',[scanId])).rows[0]?.crawling_enabled){await failJob(db,lease,{retryAfterSeconds:0});return {claimed:true,paused:true};}
  const page=await fetchMetadata({...folder,pageNumber:Number(lease.page)});
  await processPage(db,lease,page);return {claimed:true,committed:true};
 }catch(error){
  const status=Number(/HTTP (\d+)/.exec(error.message)?.[1]);
  const retryable=status?(status===408||status===429||status>=500):!/Invalid page|Invalid next page|Conflicting|replay|Pagination|Missing retained|rejected|metadata error|files\[\]/i.test(error.message);
  try{await failJob(db,lease,{retryable,retryAfterSeconds:Math.min(3600,5*2**Math.min(Number(lease.attempts)-1,10))});}catch{/* Lease was superseded or expired: recovery is database-owned. */}
  return {claimed:true,committed:false,error:'metadata_page_failure'};
 }
}
