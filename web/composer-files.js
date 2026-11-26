/* File picker and clipboard images. Upload happens with the message. */
window.createComposerFiles=function({form,input,button,list,prompt,allowed,report}){
  let pending=[];
  const urls=new Set();
  function paint(){
    for(const url of urls)URL.revokeObjectURL(url);urls.clear();
    list.replaceChildren();list.hidden=!pending.length;
    pending.forEach((file,index)=>{
      const card=document.createElement('div');card.className='ag-file';
      if(/^image\/(png|jpeg|webp|gif|bmp)$/.test(file.type)){
        const img=document.createElement('img'),url=URL.createObjectURL(file);urls.add(url);
        img.src=url;img.alt=file.name;card.append(img);
      }
      const name=document.createElement('span');name.textContent=file.name+' · '+(file.size/1024/1024).toFixed(1)+' MB';card.append(name);
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除 '+file.name);
      remove.onclick=()=>{if(allowed()){pending.splice(index,1);paint();}};
      card.append(remove);list.append(card);
    });
  }
  function add(files){
    if(!allowed())return;
    const all=[...pending,...files];
    if(all.length>3||all.some(f=>f.size>10*1024*1024)||all.reduce((n,f)=>n+f.size,0)>15*1024*1024){
      report('附件最多 3 个，单个不超过 10 MB，合计不超过 15 MB');return;
    }
    if(files.some(f=>!f.size)){report('不能上传空文件');return;}
    pending=all;paint();
  }
  button.onclick=()=>{if(allowed())input.click();};
  input.onchange=()=>{add([...input.files]);input.value='';};
  form.addEventListener('paste',event=>{
    if(!allowed()||!event.clipboardData)return;
    const files=[...event.clipboardData.items].filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);
    if(!files.length)return;event.preventDefault();
    const stamp=new Date().toISOString().replace(/[:.]/g,'-');
    add(files.map((f,i)=>new File([f],`截图-${stamp}-${i+1}.${({'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif','image/bmp':'bmp'})[f.type]||'png'}`,{type:f.type})));
  });
  return {files:()=>pending.slice(),clear(){pending=[];input.value='';paint();},
    encode:files=>Promise.all(files.map(file=>new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onerror=()=>reject(Error('附件读取失败，请重新选择'));
      reader.onload=()=>resolve({name:file.name,mime:file.type||'application/octet-stream',data:String(reader.result).split(',')[1]});
      reader.readAsDataURL(file);
    })))};
};
