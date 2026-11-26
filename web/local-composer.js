const localComposer=document.querySelector('#composer'),localPrompt=document.querySelector('#prompt'),localSend=document.querySelector('#send'),localFiles=document.querySelector('#files'),localFileList=document.querySelector('#file-list'),localTaskStrip=document.querySelector('#task-strip');
const localShowTask=(message,error=false)=>{localTaskStrip.hidden=!message;localTaskStrip.textContent=message||'';localTaskStrip.className=`task-strip${error?' error':''}`;};
const localFileData=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,mime:file.type||'application/octet-stream',data:String(reader.result).split(',',2)[1]||''});reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file);});
function clearLocalFiles(){localFiles.value='';localFileList.hidden=true;localFileList.innerHTML='';}
localFiles.onchange=()=>{const files=[...localFiles.files];localFileList.hidden=!files.length;if(!files.length)return;localFileList.innerHTML=`<span>待发送：${files.map(file=>`${esc(file.name)}（${(file.size/1024/1024).toFixed(1)} MB）`).join('、')} · 可继续填写描述</span><button type="button">取消附件</button>`;localFileList.querySelector('button').onclick=clearLocalFiles;};
document.addEventListener('paste',event=>{
  if(!selected||!event.clipboardData)return;
  const target=event.target;
  if(target!==localPrompt&&target!==document.body&&!localComposer.contains(target))return;
  const imageTypes={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif','image/bmp':'bmp'};
  const pasted=[...event.clipboardData.items].filter(item=>item.kind==='file'&&imageTypes[item.type]).map(item=>item.getAsFile()).filter(Boolean);
  if(!pasted.length)return;
  event.preventDefault();
  const existing=[...localFiles.files],all=[...existing,...pasted];
  if(all.length>3||all.some(file=>file.size>10*1024*1024)||all.reduce((sum,file)=>sum+file.size,0)>15*1024*1024){localShowTask('附件限制：最多3个，单文件10 MB，单次总计15 MB',true);return;}
  const transfer=new DataTransfer(),stamp=new Date().toISOString().replace(/[:.]/g,'-');
  existing.forEach(file=>transfer.items.add(file));
  pasted.forEach((file,index)=>transfer.items.add(new File([file],`截图-${stamp}${pasted.length>1?`-${index+1}`:''}.${imageTypes[file.type]}`,{type:file.type})));
  localFiles.files=transfer.files;localFiles.onchange();localShowTask('截图已添加，点击发送');
});
localPrompt.oninput=()=>{localPrompt.style.height='auto';localPrompt.style.height=Math.min(localPrompt.scrollHeight,180)+'px';};
localPrompt.onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();localComposer.requestSubmit(localSend);}};
async function responseJson(response){const type=response.headers.get('content-type')||'';if(!type.includes('application/json'))throw new Error(response.status===404?'本地输入接口尚未加载，请重启桥后刷新网页':'本地桥返回了无法识别的响应');return response.json();}
const localTrackedTasks=new Map();
let localRenderedThread=null;
const localTerminalStates = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

function localTaskMessage(task, thread) {
  if (task.status === 'interrupted' && task.turnId && thread?.id === task.threadId) {
    const turn = thread.turns?.find(item => item.id === task.turnId && item.status === 'interrupted');
    if (turn) {
      const hasReply = turn.items?.some(item => item.type === 'agentMessage' && ['final_answer', 'final'].includes(item.phase));
      return hasReply ? '本轮已中断 · 已有回复，请确认结果是否完整' : '本轮已中断，未生成最终答案';
    }
  }
  if (task.steered && task.status === 'running') return 'DeepSeek Harness 仍在处理 · 已追加消息';
  return task.message || task.status;
}
function renderLocalTracked(){
  const record=localTrackedTasks.get(selected);
  if(!record){if(localRenderedThread!==selected)localShowTask('');localRenderedThread=selected;return;}
  localRenderedThread=selected;
  localShowTask(record.message,record.status==='failed');
  if(['interrupted','cancelled'].includes(record.status))localTaskStrip.className='task-strip '+(record.status==='interrupted'?'interrupted':'cancelled');
}
async function pollLocalTask(taskId, threadId) {
  const record = {id:taskId, message:'已提交到本机桥', status:'running'};
  localTrackedTasks.set(threadId, record);
  const current = () => localTrackedTasks.get(threadId) === record;
  let delay = 1000;
  while (current()) {
    await new Promise(resolve => setTimeout(resolve, delay));
    if (!current()) return;
    try {
      const response = await bridgeFetch(`/api/local-task/${encodeURIComponent(taskId)}`, {cache:'no-store'});
      const task = await responseJson(response);
      if (!current()) return;
      if (!response.ok) {
        if (response.status === 404 && task.code === 'task_not_found') {
          record.status = 'unknown';
          record.message = '本机已不再保留这条任务状态，请查看对话记录。';
          if (selected === threadId) renderLocalTracked();
          return;
        }
        throw new Error(task.error || '任务状态读取失败');
      }
      record.status = task.status;
      record.message = localTaskMessage(task, currentThread);
      if (selected === threadId) renderLocalTracked();
      if (localTerminalStates.has(task.status)) {
        if (selected === threadId) await loadThread(threadId, true);
        if (current() && selected === threadId) {
          record.message = localTaskMessage(task, currentThread);
          renderLocalTracked();
        }
        return;
      }
      delay = task.status === 'unknown' || document.hidden ? 10000 : 2000;
    } catch {
      if (!current() || localTerminalStates.has(record.status)) return;
      record.message = '任务状态暂时无法读取，正在重试…';
      record.status = 'unknown';
      if (selected === threadId) renderLocalTracked();
      delay = Math.min(delay * 2, 15000);
    }
  }
}
setInterval(renderLocalTracked,1000);
localComposer.onsubmit=async event=>{event.preventDefault();const text=localPrompt.value.trim(),chosen=[...localFiles.files];if(!selected){localShowTask('请先选择一个对话',true);return;}if(!text&&!chosen.length)return;if(chosen.length>3||chosen.some(file=>file.size>10*1024*1024)||chosen.reduce((sum,file)=>sum+file.size,0)>15*1024*1024){localShowTask('附件限制：最多3个，单文件10 MB，单次总计15 MB',true);return;}localSend.disabled=true;try{const threadId=selected;const files=await Promise.all(chosen.map(localFileData)),response=await bridgeFetch('/api/local-tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({threadId,text:text||'请查看并处理我从本地网页上传的附件。',files})}),data=await responseJson(response);if(!response.ok)throw new Error(data.message||data.error||'发送失败');localPrompt.value='';localPrompt.style.height='';clearLocalFiles();localShowTask('已提交到本机桥');pollLocalTask(data.taskId,threadId);}catch(error){localShowTask(error.message,true);}finally{localSend.disabled=false;localPrompt.focus();}};

const localNewButton=document.querySelector('#new-thread'),localNewDialog=document.querySelector('#new-thread-dialog'),localNewForm=document.querySelector('#new-thread-form'),localNewProject=document.querySelector('#new-thread-project'),localNewProjectName=document.querySelector('#new-project-name'),localNewTitle=document.querySelector('#new-thread-title'),localNewText=document.querySelector('#new-thread-text'),localExistingRow=document.querySelector('#existing-project-row'),localProjectRow=document.querySelector('#new-project-row'),localNewLocation=document.querySelector('#new-thread-location');
const localCreateGuard=window.createThreadGuard({form:localNewForm,dialog:localNewDialog,opener:localNewButton});
let localBridgeConfig={projectsRoot:'',standaloneDir:''};
const localThreadKind=()=>new FormData(localNewForm).get('thread-kind')||'existing';
function refreshLocalNewForm(){const kind=localThreadKind();localExistingRow.hidden=kind!=='existing';localNewProject.disabled=kind!=='existing';localProjectRow.hidden=kind!=='project';const cwd=kind==='standalone'?localBridgeConfig.standaloneDir:kind==='project'?`${localBridgeConfig.projectsRoot.replace(/[\\/]+$/,'')}\\${localNewProjectName.value.trim()||'项目名称'}`:localNewProject.value;localNewLocation.textContent=`本机目录：${cwd||'请先选择项目'}`;}
const newModel = document.querySelector('#new-thread-model');
const newEffort = document.querySelector('#new-thread-effort');
const newPermission = document.querySelector('#new-thread-permission');
const optionsStatus = document.querySelector('#new-thread-options-status');
const optionsRetry = document.querySelector('#retry-new-thread-options');
const newSubmit = localNewForm.querySelector('[type="submit"]');
let creationModels;
let optionsRequest = 0;

function updateCreationEfforts(preferred) {
  const model = creationModels.models.find(item => item.id === newModel.value);
  const efforts = model?.efforts || creationModels.efforts || [];
  newEffort.replaceChildren(...efforts.map(item => new Option(item.name || item.id, item.id)));
  for (const value of [preferred, model?.defaultEffort]) {
    if (efforts.some(item => item.id === value)) {
      newEffort.value = value;
      break;
    }
  }
  newSubmit.disabled = !newModel.value || !newEffort.value || !newPermission.value;
}

async function loadCreationOptions() {
  const request = ++optionsRequest;
  for (const select of [newModel, newEffort, newPermission]) {
    select.disabled = true;
    select.replaceChildren();
  }
  newSubmit.disabled = true;
  optionsRetry.hidden = true;
  optionsStatus.hidden = false;
  optionsStatus.textContent = '正在读取模型和权限…';
  async function read(path) {
    const response = await bridgeFetch(path, {cache: 'no-store'});
    if (!response.ok) throw new Error();
    return response.json();
  }
  try {
    const [models, permissions] = await Promise.all([read('/api/models'), read('/api/permissions')]);
    if (request !== optionsRequest || !localNewDialog.open) return;
    if (!models.models?.length || !permissions.options?.length) throw new Error();
    if (!permissions.confirmsPermissionBeforeFirstTurn) {
      optionsStatus.textContent = '新建选项等待本机 DeepSeek 桥更新。服务重启后可重新加载。';
      optionsRetry.hidden = false;
      return;
    }
    creationModels = models;
    newModel.replaceChildren(...models.models.map(item => {
      const duplicate = models.models.some(other => other.id !== item.id && other.name === item.name);
      const label = duplicate && item.provider ? `${item.name}（${item.provider}）` : item.name;
      return new Option(label || item.id, item.id);
    }));
    if (models.models.some(item => item.id === models.bridgeDefault?.model)) {
      newModel.value = models.bridgeDefault.model;
    }
    newPermission.replaceChildren(...permissions.options.map(item =>
      new Option(PERMISSION_LABELS[item.value]?.name || item.name || item.value, item.value)));
    newPermission.value = permissions.bridgeDefault || permissions.defaultPreset || '';
    if (!newPermission.value) {
      newPermission.add(new Option('请选择权限范围', ''), 0);
      newPermission.value = '';
    }
    updateCreationEfforts(models.bridgeDefault?.model === newModel.value ? models.bridgeDefault.effort : '');
    for (const select of [newModel, newEffort, newPermission]) select.disabled = false;
    optionsStatus.hidden = true;
  } catch {
    if (request !== optionsRequest || !localNewDialog.open) return;
    optionsStatus.textContent = '模型或权限选项暂时无法读取，请重试。';
    optionsRetry.hidden = false;
  }
}

newModel.onchange = () => updateCreationEfforts(newEffort.value);
newPermission.onchange = () => { newSubmit.disabled = !newModel.value || !newEffort.value || !newPermission.value; };
optionsRetry.onclick = loadCreationOptions;
window.openLocalNewThread=async function(kind='existing',cwd=null){if(localCreateGuard.busy||localNewDialog.open)return;try{localBridgeConfig=await(await bridgeFetch('/api/auth/config',{cache:'no-store'})).json();}catch{}if(localCreateGuard.busy||localNewDialog.open)return;const projects=managedProjects.length?managedProjects.map(p=>p.cwd):[...new Set(threads.map(item=>item.cwd).filter(Boolean))];localNewProject.innerHTML=projects.map(path=>`<option value="${esc(path)}">${esc(projectName(path))} — ${esc(path)}</option>`).join('');const preferred=cwd||threads.find(item=>item.id===selected)?.cwd||projects[0]||'';if(preferred)localNewProject.value=preferred;localNewForm.querySelector(`input[name="thread-kind"][value="${kind}"]`).checked=true;localNewTitle.value='';localNewText.value='';localNewProjectName.value='';refreshLocalNewForm();localNewDialog.showModal();loadCreationOptions();setTimeout(()=>localNewTitle.focus(),50);};
localNewForm.querySelectorAll('input[name="thread-kind"]').forEach(input=>input.onchange=refreshLocalNewForm);localNewProject.onchange=refreshLocalNewForm;localNewProjectName.oninput=refreshLocalNewForm;
document.querySelector('#close-new-thread').onclick=()=>localNewDialog.close();document.querySelector('#cancel-new-thread').onclick=()=>localNewDialog.close();localNewButton.onclick=()=>window.openLocalNewThread('existing');
localNewForm.onsubmit=async event=>{event.preventDefault();if(localCreateGuard.busy||newSubmit.disabled)return;const kind=localThreadKind(),project=localNewProjectName.value.trim();if(kind==='project'&&(!project||/[<>:"/\\|?*]/.test(project))){localShowTask('项目名称不能为空，也不能包含路径特殊字符',true);return;}const cwd=kind==='standalone'?localBridgeConfig.standaloneDir:kind==='project'?`${localBridgeConfig.projectsRoot.replace(/[\\/]+$/,'')}\\${project}`:localNewProject.value,title=localNewTitle.value.trim(),text=localNewText.value.trim();if(!cwd||!title||!text)return;if(!localCreateGuard.start())return;try{const model=newModel.value,effort=newEffort.value,permissionPreset=newPermission.value;const response=await bridgeFetch('/api/local-tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'new_thread',cwd,title,text,model,effort,permissionPreset})}),data=await responseJson(response);if(!response.ok)throw Object.assign(new Error(data.message||data.error||'创建失败'),{creationConfirmed:response.status>=400&&response.status<500&&response.status!==408});const created=await localCreateGuard.local(data,bridgeFetch);localCreateGuard.succeed();els.title.textContent=title;els.meta.textContent=`正在创建 · 项目：${projectName(cwd)}`;els.messages.innerHTML='<div class="loading">正在创建新对话并发送第一条消息…</div>';localShowTask('正在创建新对话');pollLocalTask(data.taskId,created.threadId);loadThread(created.threadId);loadThreads(false);}catch(error){localCreateGuard.fail(error);localShowTask(error.message,true);}};
