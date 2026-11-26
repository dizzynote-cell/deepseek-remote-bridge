const el=id=>document.getElementById(id);
let selected='',lastReplyModel='',busy=false;
const historyPageSize=6;
let visibleTurns=historyPageSize, historyRequest=0, lastHistory='', paging=false;
let catalog=[],lastDesktopRequest=null;
let libraryEnabled=false,showHidden=false,attachmentsSupported=false;
const attachments=window.createComposerFiles({form:el('composer'),input:el('files'),button:el('attach'),list:el('file-list'),prompt:el('prompt'),
  allowed:()=>attachmentsSupported&&!!selected&&!el('send').disabled,report:message=>report(Error(message))});
const libraryBar=node('div',''),hiddenToggle=node('button','已隐藏对话');
libraryBar.className='thread-list-controls';libraryBar.style.display='none';
hiddenToggle.type='button';libraryBar.append(hiddenToggle);el('threads').before(libraryBar);
hiddenToggle.onclick=()=>{showHidden=!showHidden;paintSessions();};
function organize(row){
  const box=window.threadBrowser.modal(row.title||'整理对话');
  const body=box.querySelector('.thread-library-body'),note=box.querySelector('.thread-library-note');
  note.textContent='隐藏仅影响桥的列表，可在“已隐藏对话”中恢复。';
  const actions=row.hidden?[['恢复到列表',{hidden:false}]]:[
    [row.pinned?'取消置顶':'置顶这个对话',{pinned:!row.pinned}],['从列表隐藏',{hidden:true}]];
  for(const [label,patch] of actions){
    const action=node('button',label);action.type='button';body.append(action);
    action.onclick=async()=>{
      const buttons=[...body.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
      try{
        const saved=await api('library',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({threadId:row.id,...patch})});
        const current=catalog.find(item=>item.id===row.id);
        if(current)Object.assign(current,{hidden:!!saved.hidden,pinned:!!saved.pinned});
        box.close();paintSessions();
      }catch(e){note.textContent=e.message;buttons.forEach(b=>b.disabled=false);}
    };
  }
}
const newKind=()=>document.querySelector('#new-form [name="thread-kind"]:checked').value;
el('new-form').addEventListener('change',()=>{
  el('existing-project-row').hidden=newKind()!=='existing';
  el('new-project-row').hidden=newKind()!=='project';
});
el('refresh').onclick=()=>sessions().catch(report);
el('search').oninput=paintSessions;
async function api(path, options){
  const r=await fetch('api/antigravity/'+path,{cache:'no-store',...options});
  if(r.status===401){location.assign('/signed-out.html');throw Error('请重新登录');}
  const data=await r.json();if(!r.ok)throw Object.assign(Error(data.error||'读取失败'),{code:data.code});return data;
}
function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
async function sessions(){
  const data=await api('sessions');
  catalog=data.sessions;libraryEnabled=!!data.threadLibrary;attachmentsSupported=!!data.attachments;
  libraryBar.style.display=libraryEnabled?'':'none';
  el('permission').parentElement.hidden=!data.cliPermissions;
  el('permission').parentElement.style.display=data.cliPermissions?'':'none';
  const previous=el('new-thread-project').value;
  el('new-thread-project').replaceChildren(...data.projects.map(name=>new Option(name,name)));
  if(data.projects.includes(previous))el('new-thread-project').value=previous;
  paintSessions();
}
function paintSessions(){
  const query=el('search').value.trim().toLowerCase();
  hiddenToggle.textContent=showHidden?'返回对话列表':'已隐藏对话';
  hiddenToggle.setAttribute('aria-pressed',String(showHidden));
  const rows=catalog.filter(row=>!!row.hidden===showHidden&&[row.title,row.cwd].join(' ').toLowerCase().includes(query))
    .sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned));
  el('count').textContent=rows.length+' 个会话';
  el('threads').replaceChildren(...rows.map(row=>{
    const b=node('button','');b.className='thread'+(selected===row.id?' active':'');
    const title=node('div',row.title);title.className='thread-title';
    const meta=node('div',row.cwd||'');meta.className='thread-meta';
    if(row.id.startsWith('desktop:')){
      const origin=node('span','桌面会话');origin.className='desktop-origin';
      meta.prepend(origin,row.cwd?' · ':'');
    }
    if(row.pinned){const pin=node('span','置顶 ');pin.className='thread-pin';title.prepend(pin);}
    b.append(title,meta);b.onclick=()=>openThread(row.id);
    if(!libraryEnabled)return b;
    const wrap=node('div',''),menu=node('button','•••');
    wrap.className='thread-list-row';menu.className='thread-menu';menu.type='button';
    menu.setAttribute('aria-label','整理对话：'+row.title);menu.onclick=()=>organize(row);
    wrap.append(b,menu);return wrap;
  }));
}
function openThread(id){
  if(selected!==id){el('prompt').value='';attachments.clear();}
  window.voiceUI.clear(id);
  selected=id;visibleTurns=historyPageSize;paging=false;lastHistory='';el('history').replaceChildren();el('notice').textContent='';
  el('runtime-mode').textContent='模式：网页';el('runtime-mode').className='mode local';el('input-state').textContent='检测中';el('send').disabled=true;el('attach').disabled=true;document.body.classList.remove('sidebar-open');paintSessions();
  const row=catalog.find(item=>item.id===id);
  lastReplyModel='';el('reply-model').hidden=true;
  el('prompt').disabled=!id;
  document.querySelector('.control-bar').style.display=id.startsWith('desktop:')?'none':'';
  el('heading').textContent=row?.title||'新会话';el('meta').textContent=row?.cwd||'';
  if(row||!id){el('model').value=row?.model||'';el('mode').value=row?.mode||'default';el('permission').value=row?.permission||'default';}
  syncChips();refresh().catch(report);
}
function report(e){el('notice').textContent=e.message;}
async function refresh(showOlder=false){
  const id=selected;if(!id||(paging&&!showOlder))return;
  const request=++historyRequest;
  let history;
  try{history=await api('history/'+encodeURIComponent(id)+'?limit='+visibleTurns);}
  catch(error){
    if(id===selected&&request===historyRequest&&showOlder)throw error;
    if(id===selected&&request===historyRequest){el('send').disabled=true;el('input-state').textContent='状态未知';throw error;}
    return;
  }
  const {turns,more,remaining,readOnly,supportsAll}=history;
  if(id!==selected||request!==historyRequest)return;
  lastReplyModel=history.lastReplyModel||'';
  syncChips();
  el('send').disabled=Boolean(readOnly)||busy||history.desktopBusy||turns.at(-1)?.status==='running';
  el('attach').disabled=!attachmentsSupported||el('send').disabled;
  el('attach').title=attachmentsSupported?'上传文件或粘贴截图':'附件功能等待桥服务更新';
  const turn=turns.at(-1);
  const conflict=turn?.status==='failed' && /(?:session|conversation).{0,80}(?:already (?:owned|locked)|owned by another|active writer)|会话.{0,20}(?:被占用|正在被其他进程使用)/i.test(turn.reply||'');
  el('runtime-mode').textContent=conflict?'模式：网页 · 上次会话占用':'模式：网页';
  el('runtime-mode').className='mode '+(conflict?'desktop':'local');
  el('input-state').textContent=readOnly?'桌面会话 · 只读':busy?'提交中':history.desktopBusy||turn?.status==='running'?'处理中':conflict?'可重试':'可输入';
  const signature=JSON.stringify([turns,more,remaining,readOnly,supportsAll,history.desktopBusy]);if(signature===lastHistory)return;lastHistory=signature;
  const h=el('history'), bottom=h.scrollHeight-h.clientHeight-h.scrollTop<80;
  const labels={running:'正在处理…',completed:'已完成',failed:'执行失败',unknown:'任务状态待核实',cancelled:'已取消',interrupted:'已中断',waiting:'等待输入'};
  h.replaceChildren(...turns.map((t,index)=>{
    const turn=node('article','');turn.className='turn';
    for(const [role,text] of [['user',t.prompt],['assistant',t.reply||labels[t.status]||t.status]]){
      const block=node('div','');block.className=role;
      const label=node('div',role==='user'?'你':'Antigravity');label.className='label';
      const bubble=node('div',text);bubble.className='bubble';
      if(role==='assistant'){const md=node('div','');md.className='markdown-body';md.innerHTML=window.renderMarkdown(text);bubble.replaceChildren(md);}
      block.append(label,bubble);
      if(role==='assistant' && t.reply){
        const voice=window.voiceUI.record({id:id+'-'+(t.id??index),status:t.status},null,t.reply,'final');
        if(voice){const actions=node('div','');actions.className='voice-actions';actions.innerHTML=voice;block.append(actions);}
      }
      turn.append(block);
    }
    return turn;
  }));
  window.voiceUI.bind(id).catch(report);
  if(id.startsWith('desktop:')&&!turns.length)h.append(node('p','桌面客户端尚未索引此会话的正文。'));
  if(more){
    const controls=node('div','');
    controls.className='older-turns-wrap';
    const label='查看更早 6 轮'+(Number.isInteger(remaining)?`（还有 ${remaining} 轮）`:'');
    const older=node('button',label),all=node('button','查看全部');
    older.type=all.type='button';
    older.onclick=()=>loadOlder(id,visibleTurns+historyPageSize,controls);
    all.onclick=()=>loadOlder(id,'all',controls);
    controls.append(older);
    if(supportsAll)controls.append(all);
    h.prepend(controls);
  }
  el('notice').textContent=readOnly?'当前只能读取桌面历史，无法向这条对话发送消息。':labels[turns.at(-1)?.status]||'';
  if(showOlder)h.scrollTop=0;
  else if(bottom)h.scrollTop=h.scrollHeight;

}
async function loadOlder(id, limit, controls){
  if(paging||selected!==id)return;
  const previous=visibleTurns, request=historyRequest+1;
  paging=true;
  visibleTurns=limit;
  const buttons=[...controls.querySelectorAll('button')];
  buttons.forEach(button=>button.disabled=true);
  try{
    await refresh(true);
  }catch(error){
    if(selected===id&&historyRequest===request){
      visibleTurns=previous;
      report(error);
    }
  }finally{
    if(selected===id&&historyRequest===request)paging=false;
    buttons.forEach(button=>button.disabled=false);
  }
}
const dialog=el('new-dialog'), settings=['model','permission','mode'];
const chipMenus=settings.map(key=>{
  const select=el(key),wrap=node('div',''),chip=node('button',''),menu=node('div',''),options=node('div','');
  wrap.className='control-wrap';chip.className='control-chip';chip.type='button';
  chip.setAttribute('aria-haspopup','menu');chip.setAttribute('aria-label',select.parentElement.firstChild.textContent);
  chip.setAttribute('aria-expanded','false');menu.className='control-menu';menu.hidden=true;
  options.className='control-options';options.setAttribute('role','menu');
  menu.append(options);wrap.append(chip,menu);select.after(wrap);select.hidden=true;
  function paint(){
    options.replaceChildren(...[...select.options].map(o=>{
      const option=node('button','');option.type='button';option.className='control-option'+(o.selected?' current':'');
      option.setAttribute('role','menuitemradio');option.setAttribute('aria-checked',String(o.selected));
      option.append(node('strong',o.text));option.onclick=()=>{select.value=o.value;syncChips();closeChips();};return option;
    }));
  }
  return {chip,menu,paint,select};
});
function syncChips(){
  const desktop=selected.startsWith('desktop:');
  const model=el('model');
  const lastName=[...model.options].find(o=>o.value&&o.value===lastReplyModel)?.text||lastReplyModel;
  const strip=el('reply-model');
  strip.hidden=!desktop;
  strip.textContent=desktop?(lastName?'最近回复模型 · '+lastName:'暂无回复模型记录')+'　·　在桌面端切换':'';
  for(const {chip,select} of chipMenus){
    let label=select.selectedOptions[0]?.text||'默认';
    if(select===model&&!desktop&&!select.value&&lastName)label+=' · '+lastName;
    chip.textContent=label;
    chip.title=label;
  }
}
const closeChips=window.bindBridgeControlMenus(chipMenus);syncChips();
function openNewDialog(){
  if(busy)return;
  el('new-controls').replaceChildren(...settings.filter(key=>key!=='permission'||!el(key).parentElement.hidden).map(key=>{
    const label=node('label',el(key).parentElement.firstChild.textContent),select=el(key).cloneNode(true);select.hidden=false;label.append(select);
    select.id='new-'+key;select.value=selected||key==='permission'?select.options[0].value:el(key).value;return label;
  }));
  document.body.classList.remove('sidebar-open');el('new-error').textContent='';dialog.showModal();
}
el('new-thread').onclick=()=>openNewDialog();
for(const id of ['close-new','cancel-new'])el(id).onclick=()=>{if(!busy)dialog.close();};
dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
async function send(event){
  event.preventDefault();if(busy)return;
  const creating=event.currentTarget.id==='new-form';
  const id=creating?'':selected;if(!creating&&(!id||el('send').disabled))return;
  const field=el(creating?'new-thread-text':'prompt'),prompt=field.value.trim();
  const chosen=creating?[]:attachments.files();if(!prompt&&!chosen.length)return;
  const form=event.currentTarget, controls=[...form.elements].map(c=>[c,c.disabled]);
  busy=true;el('input-state').textContent='提交中';for(const [c] of controls)c.disabled=true;
  let accepted=false,failure=null;
  try{
    const kind=newKind(), prefix=creating?'new-':'';
    const payload={id,prompt,model:el(prefix+'model').value,mode:el(prefix+'mode').value,
      project:kind==='project'?el('new-project-name').value.trim():el('new-thread-project').value,newProject:kind==='project',standalone:kind==='standalone',title:el('new-thread-title').value};
    if(!id.startsWith('desktop:')&&!el('permission').parentElement.hidden)payload.permission=el(prefix+'permission').value;
    payload.files=await attachments.encode(chosen);
    const draft=JSON.stringify([prompt,payload.files]);
    if(id.startsWith('desktop:')){
      if(lastDesktopRequest?.id!==id||lastDesktopRequest?.draft!==draft){
        lastDesktopRequest={id,draft,key:Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')};
      }
      payload.requestId=lastDesktopRequest.key;
    }
    const d=await api('send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    accepted=true;if(id.startsWith('desktop:'))lastDesktopRequest=null;field.value='';if(!creating&&selected===id)attachments.clear();
    if(creating){dialog.close();el('new-thread-title').value='';el('new-project-name').value='';}
    await sessions();if(creating||selected===id)openThread(d.id);
  }catch(e){
    if(e.code==='delivery_rejected')lastDesktopRequest=null;
    failure=e;(creating && dialog.open?el('new-error'):el('notice')).textContent=e.message;
  }
  finally{busy=false;for(const [c,disabled] of controls)c.disabled=disabled;
    if(accepted)el('send').disabled=true;if(!selected)el('input-state').textContent='待选会话';refresh().then(()=>{if(failure&&!creating)report(failure);}).catch(report);}
}
el('composer').onsubmit=el('new-form').onsubmit=send;
el('prompt').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!el('send').disabled){e.preventDefault();el('composer').requestSubmit();}};
let loadingQuota=false, quotaUpdated=0;
async function loadQuota(){
  if(loadingQuota||Date.now()-quotaUpdated<15000)return;
  loadingQuota=true;
  const pane=el('limits');pane.textContent='正在读取各项额度…';
  try{
    const {report}=await api('usage');
    const lines=report.replace(/\x1b\[[0-9;]*[A-Za-z]/g,'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    const meters=[];
    pane.replaceChildren(...lines.map(line=>{
      const card=node('p',line);
      const [group,windowName,remaining,reset]=line.split('\t');
      if(group && windowName && remaining){
        const period=windowName.replace(/Five Hour Limit Remaining/i,'5 小时剩余')
          .replace(/Weekly Limit Remaining|Weekly Limit/i,'每周剩余');
        card.replaceChildren(node('strong',group),node('span',period+'　'+remaining));
        const time=new Date(reset);
        if(Number.isFinite(time.getTime()))card.append(node('small','重置于 '+time.toLocaleString('zh-CN')));
      }
      for(const percent of line.matchAll(/(\d+(?:\.\d+)?)\s*%/g)){
        if(Number(percent[1])>100)continue;
        const bar=document.createElement('meter');
        bar.min=0;bar.max=100;bar.value=Number(percent[1]);
        if(/weekly|每周|周额度/i.test(windowName||line))bar.classList.add('weekly-quota');
        bar.title=line;bar.setAttribute('aria-label',line);card.append(bar);
        meters.push(bar.cloneNode());
      }
      return card;
    }));
    if(meters.length){
      const preview=el('quota-preview');
      preview.replaceChildren(...meters);
      el('quota').setAttribute('aria-label','查看 '+meters.length+' 项额度');
    }else{el('quota-preview').textContent='▥';el('quota').setAttribute('aria-label','查看额度明细');}
    quotaUpdated=Date.now();
    if(!lines.length)pane.textContent='当前账号未返回额度数据';
  }
  catch(e){pane.textContent=e.message;if(!quotaUpdated)el('quota-preview').textContent='暂不可用';}
  finally{loadingQuota=false;}
}
window.bindBridgeAccountPanel(el('quota'),el('quota-details'),loadQuota);
async function start(){
  if(!['127.0.0.1','localhost','[::1]'].includes(location.hostname))await window.sessionUI.remoteChoice();
  await sessions();
  loadModels();
  loadQuota();
  async function refreshVoice(){
    window.voiceUI.clear(selected);
    lastHistory='';
    await refresh();
  }
  window.voiceUI.boot(refreshVoice).then(refreshVoice).catch(report);
  setInterval(()=>{if(!document.hidden)loadQuota();},60000);
}
start().catch(report);setInterval(()=>{if(!document.hidden)refresh().catch(report);},4000);
function loadModels(){
  return api('models').then(({models})=>{
    const select=el('model'), value=select.value || catalog.find(row=>row.id===selected)?.model || '';
    select.replaceChildren(new Option('账号默认',''),...models.map(m=>new Option(m.name,m.id)));
    if(models.some(m=>m.id===value))select.value=value;syncChips();
    const pending=el('new-model');if(pending){const chosen=pending.value;pending.replaceChildren(...[...select.options].map(o=>o.cloneNode(true)));pending.value=chosen;}
  }).catch(()=>{});
}
