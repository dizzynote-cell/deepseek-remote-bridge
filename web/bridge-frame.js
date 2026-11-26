/* Shared interaction and placement for bridge workbenches. */
(() => {
// Provider templates supply the actions and content. The surrounding frame is shared.
const shell=document.querySelector('main.shell');
const slots=new Map([...shell.querySelectorAll(':scope > template[data-frame-slot]')].map(t=>[t.dataset.frameSlot,t]));
function region(tag, className, parent, slot){
  const element=document.createElement(tag);
  element.className=className;
  parent.append(element);
  if(slot)element.append(slots.get(slot).content);
  return element;
}
const backdrop=region('div','sidebar-backdrop',shell);
backdrop.id='sidebar-backdrop';
const sidebar=region('aside','sidebar',shell);
region('header','brand',sidebar,'brand');
const sidebarFields=document.createElement('template');
sidebarFields.dataset.bridgeSidebar='';
sidebar.append(sidebarFields,slots.get('side').content);
const conversation=region('section','conversation',shell);
const head=region('header','conversation-head',conversation);
const menuButton=region('button','menu-button',head);
menuButton.id='menu';menuButton.type='button';menuButton.textContent='会话';
menuButton.setAttribute('aria-label','打开会话列表');
head.append(slots.get('heading').content);
conversation.append(slots.get('conversation').content);
for(const slot of slots.values())slot.remove();

// The two workbenches mount the same sidebar controls and creation fields.
function mount(selector, markup) {
  const slot = document.querySelector(selector);
  if (!slot) return;
  slot.innerHTML = markup;
  if (slot.hasAttribute('data-title-optional')) slot.content.querySelector('#new-thread-title').required = false;
  slot.replaceWith(slot.content);
}
mount("[data-bridge-sidebar]", `<label class="search"><span>⌕</span><input id="search" placeholder="搜索会话或项目"></label>
      <div class="summary"><span id="count">0 个会话</span><span><button id="new-thread">新建</button><button id="refresh">刷新</button></span></div>
      <nav id="threads" class="threads"></nav>`);
mount("[data-bridge-new-fields]", `<div class="new-thread-kinds">
          <label><input type="radio" name="thread-kind" value="existing" checked> 现有项目</label>
          <label><input type="radio" name="thread-kind" value="project"> 新建项目</label>
          <label><input type="radio" name="thread-kind" value="standalone"> 独立会话</label>
        </div>
        <label id="existing-project-row">项目<select id="new-thread-project"></select></label>
        <label id="new-project-row" hidden>项目名称<input id="new-project-name" maxlength="80" placeholder="例如：桥接联调"></label>
        <label>会话名称<input id="new-thread-title" maxlength="100" required></label>
        <label>第一条消息<textarea id="new-thread-text" rows="4" required placeholder="输入这个会话要开始处理的内容"></textarea></label>`);

const mobileDevice = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && Math.min(screen.width, screen.height) < 900);
const narrowViewport = window.matchMedia('(max-width: 800px)');
const applyCompactLayout = () => document.body.classList.toggle('mobile-ui', mobileDevice || narrowViewport.matches);
applyCompactLayout();
narrowViewport.addEventListener?.('change', applyCompactLayout);
document.querySelector('#menu').onclick = () => document.body.classList.add('sidebar-open');
document.querySelector('#sidebar-backdrop').onclick = () => document.body.classList.remove('sidebar-open');

window.bindBridgeControlMenus = function(controlMenus) {
function closeControlMenus() {
  controlMenus.forEach(({ chip, menu }) => {
    menu.hidden = true;
    chip.setAttribute('aria-expanded', 'false');
  });
}

function positionControlMenu(chip, menu) {
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
  const rect = chip.getBoundingClientRect();
  const menuWidth = Math.min(300, width - 16);
  Object.assign(menu.style, {position: 'fixed', width: `${menuWidth}px`, bottom: 'auto',
    left: `${Math.max(left + 8, Math.min(rect.left, left + width - menuWidth - 8))}px`,
    maxHeight: `${Math.max(48, height - 16)}px`, overflowY: 'auto'});
  const above = rect.top - top - 8, below = top + height - rect.bottom - 8;
  const preferAbove = above >= Math.min(menu.getBoundingClientRect().height, 180) || above >= below;
  menu.style.maxHeight = `${Math.max(48, (preferAbove ? above : below) - 8)}px`;
  const menuHeight = menu.getBoundingClientRect().height;
  const wanted = preferAbove ? rect.top - menuHeight - 8 : rect.bottom + 8;
  menu.style.top = `${Math.max(top + 8, Math.min(wanted, top + height - menuHeight - 8))}px`;
}

controlMenus.forEach(({ chip, menu, paint }) => {
  // Escape the mobile row's scrolling/clipping context, keeping real buttons.
  document.body.append(menu);
  chip.onclick = event => {
    event.stopPropagation();
    const open = menu.hidden;
    closeControlMenus();
    menu.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
    if (open) { paint(); positionControlMenu(chip, menu); }
  };
  menu.onclick = event => event.stopPropagation();
});
const repositionControlMenus = () => controlMenus.forEach(({chip, menu}) => {
  if (!menu.hidden) positionControlMenu(chip, menu);
});
window.addEventListener('resize', repositionControlMenus);
window.visualViewport?.addEventListener('resize', repositionControlMenus);
window.visualViewport?.addEventListener('scroll', repositionControlMenus);
document.addEventListener('click', closeControlMenus);
document.addEventListener('keydown', event => { if(event.key==='Escape')closeControlMenus(); });
return closeControlMenus;
};
window.bindBridgeAccountPanel = function(button, panel, onOpen) {
  function close(){panel.hidden=true;button.setAttribute('aria-expanded','false');}
  button.onclick=event=>{
    event.stopPropagation();
    if(!panel.hidden){close();return;}
    panel.hidden=false;button.setAttribute('aria-expanded','true');onOpen?.();
  };
  panel.onclick=event=>event.stopPropagation();
  document.addEventListener('click',close);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
};
})();
