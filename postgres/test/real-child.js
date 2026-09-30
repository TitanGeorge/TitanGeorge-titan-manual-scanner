import {Client} from 'pg';
import {processPage,claimJob} from '../jobs.js';
import {status} from '../db.js';
const db=new Client({connectionString:process.env.TITAN_TEST_DATABASE_URL,application_name:'titan-test-child'});await db.connect();await db.query('SET search_path TO '+process.env.TITAN_TEST_SCHEMA);
try{
 const [mode,arg]=process.argv.slice(2);
 if(mode==='page')await processPage(db,JSON.parse(arg),{files:[{id:'crash-file',name:'crash.pdf',size:'10'}],nextPage:2});
 else if(mode==='resume'){const lease=await claimJob(db,'s','fresh-process');if(lease)await processPage(db,lease,{files:[{id:'after',name:'after.pdf',size:'20'}]});console.log(JSON.stringify(await status(db,'s')));}
 else console.log(JSON.stringify(await status(db,'s')));
}finally{await db.end();}
