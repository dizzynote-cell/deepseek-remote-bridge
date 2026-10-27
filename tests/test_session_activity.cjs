const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const source=fs.readFileSync(path.join(__dirname,'../adapter/index.js'),'utf8');
 const segment=source.slice(source.indexOf('  const activityCache = new Map();'),source.indexOf('  async function readSession(sessionId)'));
 let active=0,maxActive=0,calls=0;
 const records=[{header:{id:'test',createdAt:3000}},{header:{id:'work',createdAt:1000}},{header:{id:'broken',createdAt:100}}];
 const service={listSessions:async()=>records,listEvents:async id=>{
  active++;maxActive=Math.max(maxActive,active);calls++;await new Promise(r=>setTimeout(r,1));active--;
  if(id==='broken')throw Error('unavailable');
  return [{type:'user/message',time:id==='work'?5000:3000},{type:'session/title',time:99999}];
 },readTitleSnapshots:async ids=>ids.map(id=>({status:'fulfilled',value:{title:{title:id}}}))};
 const c={Map,Set,Date,Promise,Number,String,Math,ctx:{get:()=>service},clampInt:(v,d,min,max)=>Math.min(max,Math.max(min,Number(v)||d)),log:()=>{}};
 vm.runInNewContext(segment+';globalThis.read=readSessions;',c);
 const query=new URLSearchParams('limit=2');let out=await c.read(query);
 assert.equal(out.sessions.map(r=>r.id).join(','),'work,test','activity must rank before limiting');
 assert.equal(out.sessions[0].updatedAt,5000,'title changes must not count as conversation activity');
 await c.read(query);assert.equal(calls,4,'successful metadata cached; failed row retried');assert.ok(maxActive<=4);
 console.log('PASS adapter actual activity, pre-limit ranking, bounded readers, metadata cache, failure isolation');
})().catch(e=>{console.error(e);process.exitCode=1});
