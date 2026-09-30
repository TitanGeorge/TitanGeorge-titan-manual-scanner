import {readFile} from 'node:fs/promises';
import {parseCheckpoint} from './checkpoint.js';
// Dry-run has no database dependency, connection, or side effect.
const [mode,path,scanId]=process.argv.slice(2);
try{
 if(!['dry-run','import'].includes(mode)||!path)throw new Error('Usage: node cli.js dry-run checkpoint.json | import checkpoint.json SCAN_ID');
 const saved=JSON.parse(await readFile(path,'utf8'));const plan=parseCheckpoint(saved);
 if(mode==='dry-run')console.log(JSON.stringify(plan.report,null,2));
 else{
  if(!scanId||process.env.TITAN_OFFLINE_IMPORT_APPROVED!=='yes'||!process.env.TITAN_IMPORT_DATABASE_URL)throw new Error('Explicit offline import approval, isolated database URL, and scan ID required');
  const {Client}=await import('pg');const {importCheckpoint}=await import('./importer.js');const client=new Client({connectionString:process.env.TITAN_IMPORT_DATABASE_URL});await client.connect();try{console.log(JSON.stringify(await importCheckpoint(client,saved,{scanId}),null,2));}finally{await client.end();}
 }
}catch(error){if(/^Reconciliation mismatch:/.test(error.message))console.error(error.message);console.error('Offline operation failed. Check checkpoint structure, exact reconciliation, and isolated database configuration. No source requests were made.');process.exitCode=1;}
