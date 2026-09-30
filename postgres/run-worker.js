import {randomUUID} from 'node:crypto';
import {Client} from 'pg';
import {workOne} from './worker.js';
import {createMetadataTransport} from './transport.js';
// Explicit standalone command; no HTTP listener and no startup crawler in app.js.
if(process.env.TITAN_METADATA_WORKER_APPROVED!=='yes'||!process.env.TITAN_CATALOG_DATABASE_URL||!process.env.TITAN_SCAN_ID)throw new Error('Explicit worker approval, isolated database URL and scan ID required');
const db=new Client({connectionString:process.env.TITAN_CATALOG_DATABASE_URL});await db.connect();
const owner=randomUUID(),fetchMetadata=createMetadataTransport();let stopped=false;
process.once('SIGTERM',()=>{stopped=true;});process.once('SIGINT',()=>{stopped=true;});
try{while(!stopped){await workOne(db,process.env.TITAN_SCAN_ID,owner,fetchMetadata);if(!stopped)await new Promise(resolve=>setTimeout(resolve,1000));}}
finally{await db.end();}
