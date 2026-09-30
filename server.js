import express from 'express';
const app=express();
app.use(express.json());
const PORT=process.env.PORT||10000;
const state={status:'idle',folders:0,pdfs:0,bytes:0,missingSize:0,failedFolders:0,lastError:null,startedAt:null,finishedAt:null};
app.get('/',(req,res)=>res.json({name:'Titan Manual Scanner',mode:'METADATA_ONLY',downloads:false,persistentPdfStorage:false,state}));
app.get('/status',(req,res)=>res.json(state));
app.post('/scan',async(req,res)=>{
 if(state.status==='running') return res.status(409).json({error:'scan already running'});
 const {cookie,nonce}=req.body||{};
 if(!cookie||!nonce) return res.status(400).json({error:'Authenticated Service Alliance cookie and current nonce are required for a scan. They are used only in memory for this run and are not written to disk.'});
 state.status='running';state.folders=0;state.pdfs=0;state.bytes=0;state.missingSize=0;state.failedFolders=0;state.lastError=null;state.startedAt=new Date().toISOString();state.finishedAt=null;
 res.status(202).json({started:true,mode:'metadata-only'});
 scan(cookie,nonce).catch(e=>{state.status='error';state.lastError=String(e?.message||e);state.finishedAt=new Date().toISOString();});
});
const AJAX='https://servicealliancegroup.com/legacy/wp-admin/admin-ajax.php';
const ACCOUNT='11257309238295447992';
const ROOT='1-0mICiR1DYKpV73iISmEV8DcuyTe-Pc-';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function postFolder(folder,cookie,nonce){
 const body=new URLSearchParams();
 body.set('action','igd_get_files');body.set('shortcodeId','7');body.set('nonce',nonce);
 body.set('data[folder][id]',folder.id);body.set('data[folder][name]',folder.name||'');body.set('data[folder][accountId]',folder.accountId||ACCOUNT);body.set('data[folder][pageNumber]',String(folder.pageNumber||1));
 body.set('data[sort][sortBy]','name');body.set('data[sort][sortDirection]','asc');body.set('data[fileNumbers]','-1');body.set('data[limit]','0');
 const r=await fetch(AJAX,{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','cookie':cookie,'user-agent':'Titan-Metadata-Inventory/1.0'},body,redirect:'follow'});
 if(!r.ok) throw new Error('HTTP '+r.status);
 const j=await r.json();
 return j?.data??j;
}
async function getWithRetry(folder,cookie,nonce){let err;for(let i=0;i<3;i++){try{return await postFolder(folder,cookie,nonce)}catch(e){err=e;await delay(1500*(i+1));}}throw err;}
async function scan(cookie,nonce){
 const q=[{id:ROOT,name:'APPLIANCE',accountId:ACCOUNT}],seenFolders=new Set(),seenPdfs=new Set();
 while(q.length){const f=q.shift();if(seenFolders.has(f.id))continue;seenFolders.add(f.id);state.folders=seenFolders.size;let page=1;
  do{f.pageNumber=page;await delay(750);let data;try{data=await getWithRetry(f,cookie,nonce)}catch(e){state.failedFolders++;state.lastError=`${f.name||f.id}: ${e.message}`;break;}
   const files=data?.files||[];for(const x of files){const type=String(x.type||'');if(type.includes('folder')){if(x.id&&!seenFolders.has(x.id))q.push({id:x.id,name:x.name,accountId:x.accountId||ACCOUNT});continue;}if(!String(x.name||'').toLowerCase().endsWith('.pdf')||!x.id||seenPdfs.has(x.id))continue;seenPdfs.add(x.id);state.pdfs=seenPdfs.size;const size=Number(x.size);if(Number.isFinite(size)&&size>0)state.bytes+=size;else state.missingSize++;}
   page=Number(data?.nextPageNumber||0);
  }while(page>0);
 }
 state.status='complete';state.finishedAt=new Date().toISOString();
 // Deliberately retain only aggregate metadata in process memory. No PDF content, file catalog, cookies, or nonce are persisted.
}
app.listen(PORT,()=>console.log(`Titan metadata-only scanner listening on ${PORT}`));
