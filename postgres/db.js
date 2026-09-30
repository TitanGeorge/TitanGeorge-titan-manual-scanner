import {readFile,readdir} from 'node:fs/promises';
export async function migrate(db){
 await db.query('CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY)');
 for(const name of (await readdir(new URL('./migrations/',import.meta.url))).filter(n=>n.endsWith('.sql')).sort()){
  if((await db.query('SELECT name FROM schema_migrations WHERE name=$1',[name])).rows.length)continue;
  // Adopt the previously deployed schema without recreating its tables.
  if(name==='001_catalog.sql'&&(await db.query("SELECT to_regclass('scan_runs') AS existing")).rows[0].existing){await db.query('INSERT INTO schema_migrations VALUES($1)',[name]);continue;}
  const sql=await readFile(new URL('./migrations/'+name,import.meta.url),'utf8');
  await transaction(db,async()=>{const body=sql.replace(/^BEGIN;|COMMIT;\s*$/g,'');await (db.exec?db.exec(body):db.query(body));await db.query('INSERT INTO schema_migrations VALUES($1)',[name]);});
 }
}
export async function transaction(db,fn){
 await db.query('BEGIN');try{const result=await fn(db);await db.query('COMMIT');return result;}catch(error){await db.query('ROLLBACK');throw error;}
}
export async function status(db,scan){return (await db.query('SELECT * FROM scan_status WHERE scan_id=$1',[scan])).rows[0]??null;}
export async function putFile(db,file,provenance,{allowChanged=false}={}){
 const row=(await db.query(`INSERT INTO files(account_id,drive_file_id,filename,size_bytes,mime_type,source_metadata,provenance,metadata_completeness)
 VALUES($1,$2,$3,$4,$5,$6,$7,'partial') ON CONFLICT(account_id,drive_file_id) DO UPDATE SET
 size_bytes=coalesce(files.size_bytes,excluded.size_bytes),filename=coalesce(files.filename,excluded.filename),
 mime_type=coalesce(files.mime_type,excluded.mime_type),updated_at=now() RETURNING id,size_bytes`,
 [file.account,file.id,file.name,file.size,file.mime,JSON.stringify(file.metadata),provenance])).rows[0];
 if(!allowChanged&&file.size!==null&&row.size_bytes!==null&&String(row.size_bytes)!==file.size)throw new Error('Conflicting file size');return row.id;
}
