import {readFile} from 'node:fs/promises';
import {Client} from 'pg';
import {migrate,status} from './db.js';
import {initializeFresh,setPaused} from './fresh.js';
const [action,scanId,path]=process.argv.slice(2);
if(!['migrate','init','pause','enable','status'].includes(action))throw new Error('Usage: node admin.js migrate|init|pause|enable|status SCAN_ID [root.json]');
if(!process.env.TITAN_CATALOG_DATABASE_URL)throw new Error('Isolated catalog database URL required');
if(action==='enable'&&process.env.TITAN_METADATA_ENABLE_APPROVED!=='yes')throw new Error('Explicit metadata enable approval required');
const db=new Client({connectionString:process.env.TITAN_CATALOG_DATABASE_URL});await db.connect();
try{
 if(action==='migrate')await migrate(db);
 else if(action==='init')console.log(await initializeFresh(db,{scanId,root:JSON.parse(await readFile(path,'utf8'))}));
 else if(action==='pause'||action==='enable')await setPaused(db,scanId,action==='pause');
 else console.log(JSON.stringify(await status(db,scanId),null,2));
}finally{await db.end();}
