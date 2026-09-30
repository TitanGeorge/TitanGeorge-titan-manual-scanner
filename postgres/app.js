import {createServer} from 'node:http';
import {Pool} from 'pg';
import {status} from './db.js';
// Separate future service entry point. Legacy server.js and Render config are unchanged.
const pool=new Pool({connectionString:process.env.TITAN_CATALOG_DATABASE_URL,max:6});
const scanId=process.env.TITAN_SCAN_ID;if(!scanId||!process.env.TITAN_CATALOG_DATABASE_URL)throw new Error('Catalog URL and scan ID required');
const server=createServer(async(req,res)=>{
 res.setHeader('content-type','application/json');
 if(req.method!=='GET'||!['/health','/status'].includes(req.url)){res.writeHead(404);res.end('{}');return;}
 try{const db=await pool.connect();try{const s=await status(db,scanId);res.writeHead(s?200:404);res.end(JSON.stringify(req.url==='/health'?{database:!!s,authority:'postgresql'}:{...s,authority:'postgresql',pdfDownloads:0}));}finally{db.release();}}
 catch{res.writeHead(503);res.end('{"error":"persistent_status_unavailable"}');}
});
server.listen(Number(process.env.PORT||10000));
