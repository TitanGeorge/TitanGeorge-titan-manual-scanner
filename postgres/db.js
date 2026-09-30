import {readFile} from 'node:fs/promises';
export async function migrate(db){const sql=await readFile(new URL('./migrations/001_catalog.sql',import.meta.url),'utf8');await (db.exec?db.exec(sql):db.query(sql));}
export async function transaction(db,fn){
 await db.query('BEGIN');try{const result=await fn(db);await db.query('COMMIT');return result;}catch(error){await db.query('ROLLBACK');throw error;}
}
export async function status(db,scan){return (await db.query('SELECT * FROM scan_status WHERE scan_id=$1',[scan])).rows[0]??null;}
export async function putFile(db,file,provenance){
 const row=(await db.query(`INSERT INTO files(account_id,drive_file_id,filename,size_bytes,mime_type,source_metadata,provenance,metadata_completeness)
 VALUES($1,$2,$3,$4,$5,$6,$7,'partial') ON CONFLICT(account_id,drive_file_id) DO UPDATE SET
 size_bytes=coalesce(files.size_bytes,excluded.size_bytes),filename=coalesce(files.filename,excluded.filename),
 mime_type=coalesce(files.mime_type,excluded.mime_type),updated_at=now() RETURNING id,size_bytes`,
 [file.account,file.id,file.name,file.size,file.mime,JSON.stringify(file.metadata),provenance])).rows[0];
 if(file.size!==null&&row.size_bytes!==null&&String(row.size_bytes)!==file.size)throw new Error('Conflicting file size');return row.id;
}
