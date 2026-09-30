import {status} from './db.js';
// Future Express integration only. Not registered in production server.js.
// db must be a dedicated Client, or obtain a client with pool.connect() per request.
export function statusHandler(db,scanId){return async(_req,res)=>{try{const s=await status(db,scanId);if(!s)return res.status(404).json({error:'scan_not_found'});const jobs=(await db.query(`SELECT id,folder_id,page,status,attempts,lease_owner,lease_expires_at,available_after FROM crawl_jobs WHERE scan_id=$1 AND status<>'completed' ORDER BY id`,[scanId])).rows;return res.json({...s,jobs,authority:'postgresql',pdfDownloads:0});}catch{return res.status(503).json({error:'persistent_status_unavailable'});}};}
