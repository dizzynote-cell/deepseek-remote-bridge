// SPDX-License-Identifier: MIT
// Copyright (c) 2026 xiyannan
// Project: DeepSeek Remote Bridge
// Renders real DeepSeek Harness sessions: listing, transcript, tool calls,
// human approvals and the native account balance. Nothing here invents data:
// every row comes from the bridge API, which reads the harness runtime.
const els = {
  threads: document.querySelector('#threads'), search: document.querySelector('#search'), count: document.querySelector('#count'),
  title: document.querySelector('#title'), meta: document.querySelector('#meta'), messages: document.querySelector('#messages'),
  refresh: document.querySelector('#refresh'), mode: document.querySelector('#mode'), loadMore: document.querySelector('#load-more'),
  balance: document.querySelector('#balance'), balanceDetails: document.querySelector('#balance-details'),
  menu: document.querySelector('#menu'), backdrop: document.querySelector('#sidebar-backdrop'),
  providerToggle: document.querySelector('#provider-toggle'), providerMenu: document.querySelector('#provider-menu'),
  providerSibling: document.querySelector('#provider-sibling'), providerSiblingName: document.querySelector('#provider-sibling-name'),
  providerSiblingNote: document.querySelector('#provider-sibling-note'),
  settings: document.querySelector('#settings'), settingsDialog: document.querySelector('#settings-dialog'),
  closeSettings: document.querySelector('#close-settings'), settingsDone: document.querySelector('#settings-done'),
  permissionSelect: document.querySelector('#permission-select'), defaultPermission: document.querySelector('#default-permission'),
  modelChip: document.querySelector('#model-chip'), modelMenu: document.querySelector('#model-menu'),
  modelOptions: document.querySelector('#model-options'), effortOptions: document.querySelector('#effort-options'),
  modelNote: document.querySelector('#model-note'),
  permissionNote: document.querySelector('#permission-note'), activity: document.querySelector('#activity-strip')
};
let threads = [], managedProjects = [], selected = null, threadLoading = false, lastThreadSignature = '';
let nextCursor = null, threadVisibleCounts = new Map(), threadRequestSerial = 0, threadController = null, currentThread = null;
const mobileDevice = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && Math.min(screen.width, screen.height) < 900);
const narrowViewport = window.matchMedia('(max-width: 800px)');
const applyCompactLayout = () => document.body.classList.toggle('mobile-ui', mobileDevice || narrowViewport.matches);
applyCompactLayout();
narrowViewport.addEventListener?.('change', applyCompactLayout);
const esc = window.bridgeEscape;
const imageExtensions = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'ico', 'tiff', 'heic']);
const statusLabels = { completed: '已完成', failed: '失败', interrupted: '已中断', cancelled: '已取消', in_progress: '进行中', running: '进行中' };

let permissionCatalog = { options: [], defaultPreset: null, bridgeDefault: '' };

let modelCatalog = { models: [], efforts: [], bridgeDefault: { model: '', effort: '' }, session: null };
let modelSelection = { model: '', effort: '' };

function modelLabels() {
  const model = modelCatalog.models.find(item => item.id === modelSelection.model);
  const effort = modelCatalog.efforts.find(item => item.id === modelSelection.effort);
  return {
    model: model ? model.short : (modelSelection.model || '模型'),
    effort: effort ? effort.name : (modelSelection.effort || ''),
  };
}

function paintModelChip() {
  if (!els.modelChip) return;
  const labels = modelLabels();
  els.modelChip.textContent = `${labels.model}${labels.effort ? ' · ' + labels.effort : ''}`;
  els.modelChip.title = `${labels.model}${labels.effort ? ' · 强度 ' + labels.effort : ''}（点击切换）`;
}

function paintModelMenu() {
  if (!els.modelOptions || !els.effortOptions) return;
  els.modelOptions.innerHTML = modelCatalog.models.map(item => `
    <button type="button" role="menuitemradio" aria-checked="${item.id === modelSelection.model}" class="model-option ${item.id === modelSelection.model ? 'current' : ''}" data-model="${esc(item.id)}">
      <strong>${esc(item.name)}</strong><small>${esc(item.note || '')}</small>
    </button>`).join('');
  els.effortOptions.innerHTML = modelCatalog.efforts.map(item => `
    <button type="button" role="menuitemradio" aria-checked="${item.id === modelSelection.effort}" class="model-option ${item.id === modelSelection.effort ? 'current' : ''}" data-effort="${esc(item.id)}">
      <strong>${esc(item.name)}</strong><small>${esc(item.note || '')}</small>
    </button>`).join('');
  els.modelOptions.querySelectorAll('[data-model]').forEach(button => {
    button.onclick = () => applyModel(button.dataset.model, modelSelection.effort);
  });
  els.effortOptions.querySelectorAll('[data-effort]').forEach(button => {
    button.onclick = () => applyModel(modelSelection.model, button.dataset.effort);
  });
  if (els.modelNote) {
    els.modelNote.textContent = modelCatalog.note || '';
  }
}

async function loadModels(threadId) {
  try {
    const query = threadId ? `?threadId=${encodeURIComponent(threadId)}` : '';
    const data = await (await bridgeFetch('/api/models' + query)).json();
    modelCatalog = data;
    modelSelection = {
      model: data.session?.model || data.bridgeDefault?.model || '',
      effort: data.session?.effort || data.bridgeDefault?.effort || '',
    };
  } catch { /* keep the last catalog */ }
  paintModelChip();
  paintModelMenu();
}

async function applyModel(model, effort) {
  if (!model || !effort) return;
  const threadId = selected;
  els.modelChip.disabled = true;
  try {
    const response = await bridgeFetch('/api/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(threadId ? { threadId, model, effort } : { model, effort }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '切换失败');
    modelSelection = { model, effort };
    paintModelChip();
    paintModelMenu();
    showTaskNotice(threadId
      ? `本会话模型已切换为「${modelLabels().model} · ${modelLabels().effort}」（运行时已重启）`
      : `新会话默认模型：${modelLabels().model} · ${modelLabels().effort}`);
    if (threadId) await loadThread(threadId, true);
  } catch (error) {
    showTaskNotice(`模型切换失败：${error.message}`, true);
  } finally {
    els.modelChip.disabled = false;
  }
}

function projectName(cwd) { return window.projectDisplay.name(cwd, managedProjects); }

function permissionLabel(value) {
  const option = permissionCatalog.options.find(item => item.value === value);
  return option ? (option.name || option.value) : (value || '未知');
}

function fillPermissionSelect(select, value) {
  if (!select) return;
  select.innerHTML = permissionCatalog.options
    .map(option => `<option value="${esc(option.value)}">${esc(option.name || option.value)}</option>`)
    .join('');
  if (value) select.value = value;
  if (!select.value && select.options.length) select.selectedIndex = 0;
}

async function loadPermissionCatalog() {
  try {
    const data = await (await bridgeFetch('/api/permissions')).json();
    permissionCatalog = {
      options: Array.isArray(data.options) ? data.options : [],
      defaultPreset: data.defaultPreset || null,
      bridgeDefault: data.bridgeDefault || data.defaultPreset || ''
    };
  } catch { /* keep the last catalog */ }
  fillPermissionSelect(els.permissionSelect, currentThread?.bridgePermission || null);
  fillPermissionSelect(els.defaultPermission, permissionCatalog.bridgeDefault);
  if (els.permissionNote) {
    const active = permissionCatalog.options.find(item => item.value === permissionCatalog.bridgeDefault);
    els.permissionNote.textContent = active?.description
      ? `默认预设：${active.name || active.value} — ${active.description}`
      : '新会话创建时使用这个预设；已有会话在输入框左侧单独切换。';
  }
}

async function syncThreadPermission(id) {
  if (!id) return;
  try {
    const data = await (await bridgeFetch(`/api/permissions?threadId=${encodeURIComponent(id)}`)).json();
    const value = data.current || data.bridgeDefault || permissionCatalog.bridgeDefault;
    fillPermissionSelect(els.permissionSelect, value);
    els.permissionSelect.dataset.thread = id;
  } catch { /* leave the select as-is */ }
}
function statusLabel(status) {
  const type = typeof status === 'object' && status ? status.type : status;
  return { active: '当前已加载', notLoaded: '空闲', idle: '空闲', running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断' }[type] || type || '未知';
}

function syncUrl(id) {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('thread', id); else url.searchParams.delete('thread');
    window.history.replaceState(null, '', url.toString());
  } catch {}
}

function renderThreads() {
  const q = els.search.value.trim().toLowerCase();
  const filtered = threads.filter(t => `${t.name} ${projectName(t.cwd)} ${t.cwd || ''} ${t.id || ''}`.toLowerCase().includes(q));
  const groups = new Map();
  for (const t of filtered) {
    const key = window.projectDisplay.key(t);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  }
  els.count.textContent = `${filtered.length} 个会话 · ${groups.size} 个项目`;
  els.threads.innerHTML = window.projectDisplay.groups(groups, managedProjects).map(([cwd, items]) => `
    <section class="project-group">
      <button class="project-heading" data-project-cwd="${esc(window.projectDisplay.cwd(cwd, managedProjects))}" data-project-kind="${cwd === 'desktop:projectless' ? 'standalone' : 'existing'}" title="点击在此项目中新建会话">
        <span>${esc(projectName(cwd))}</span><small>${items.length} ＋</small>
      </button>
      ${items.map(t => {
        const running = t.bridgeRunning === true;
        return `<button class="thread ${selected === t.id ? 'active' : ''}" data-id="${esc(t.id)}">
          <div class="thread-title">${esc(t.name)}${running ? '<span class="thread-live" title="运行中">●</span>' : ''}</div>
          <div class="thread-meta"><span class="status-dot ${running ? 'running' : ''}"></span>${esc(running ? '运行中' : (statusLabel(t.status) === '当前已加载' ? '已载入' : '空闲'))}</div>
        </button>`;
      }).join('')}
    </section>`).join('');
  document.querySelectorAll('.project-heading').forEach(b => b.onclick = () => window.openLocalNewThread?.(b.dataset.projectKind || 'existing', b.dataset.projectCwd));
  document.querySelectorAll('.thread').forEach(b => b.onclick = () => { document.body.classList.remove('sidebar-open'); loadThread(b.dataset.id); });
  els.loadMore.hidden = !nextCursor;
}

async function loadSwitchTargets() {
  try {
    const info = await (await bridgeFetch('/api/bridge-info')).json();
    const menu = info.logoMenu || {};
    const targets = Array.isArray(menu.targets) ? menu.targets : [];
    const sibling = targets.find(target => !target.current);
    const current = targets.find(target => target.current);
    if (current?.displayName) {
      els.providerToggle.setAttribute('aria-label', `切换后端：当前 ${current.displayName}`);
    }
    if (!sibling || !els.providerSibling) return;
    els.providerSiblingName.textContent = sibling.displayName || 'Codex';
    if (sibling.enabled && sibling.url) {
      els.providerSibling.disabled = false;
      els.providerSibling.removeAttribute('aria-disabled');
      els.providerSibling.classList.add('reachable');
      els.providerSiblingNote.textContent = `切换到 ${sibling.displayName}`;
      els.providerSibling.onclick = () => { window.location.href = sibling.url; };
    } else {
      els.providerSibling.disabled = true;
      els.providerSibling.setAttribute('aria-disabled', 'true');
      els.providerSiblingNote.textContent = '未接入本页 · 由入口页统一切换';
    }
  } catch { /* the static menu stays as-is */ }
}

async function loadThreads(append = false) {
  els.refresh.disabled = true;
  els.loadMore.disabled = true;
  try {
    const url = `/api/threads?limit=50${append && nextCursor ? `&cursor=${encodeURIComponent(nextCursor)}` : ''}`;
    const d = await (await bridgeFetch(url)).json();
    const incoming = d.threads || [];
    threads = append ? [...threads, ...incoming.filter(t => !threads.some(old => old.id === t.id))] : incoming;
    managedProjects = Array.isArray(d.projects) ? d.projects : managedProjects;
    nextCursor = d.nextCursor || null;
    renderThreads();
  } finally {
    els.refresh.disabled = false;
    els.loadMore.disabled = false;
  }
}

async function loadStatus() {
  try {
    const d = await (await bridgeFetch('/api/status')).json();
    els.mode.textContent = d.shortLabel || d.label || '运行状态未知';
    els.mode.className = `mode ${d.mode || 'detecting'}`;
  } catch {
    els.mode.textContent = '运行时状态不可用';
    els.mode.className = 'mode detecting';
  }
}

const money = value => {
  const text = String(value ?? '');
  if (!/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return text;
  const number = Number(text);
  if (!Number.isFinite(number)) return text;
  return number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const currencySymbol = code => ({ CNY: '¥', USD: '$' })[code] || (code ? `${code} ` : '');
const formatTime = epoch => epoch ? new Date(epoch * 1000).toLocaleString('zh-CN', { hour12: false }) : '未知';

let pricingState = null;

function remainingText(epoch) {
  if (!epoch) return '';
  const seconds = Math.max(0, Math.round(epoch - Date.now() / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分钟`;
  return `${seconds} 秒`;
}

function paintPhase(pricing) {
  if (!pricing || !pricing.label) return;
  const badge = els.balance.querySelector('[data-balance-phase]');
  if (!badge) return;
  badge.hidden = false;
  badge.textContent = pricing.label;
  badge.className = `phase-badge ${pricing.phase}`;
  const target = pricing.phase === 'peak' ? '闲时' : '忙时';
  badge.title = `${pricing.localWeekday} ${pricing.localTime}（${pricing.zone}）· ${remainingText(pricing.nextSwitchEpoch)}后转为${target}`;
}

async function loadBalance(force = false) {
  const primary = els.balance.querySelector('[data-balance-primary]');
  const state = els.balance.querySelector('[data-balance-state]');
  const note = els.balance.querySelector('[data-balance-note]');
  try {
    const d = await (await bridgeFetch(`/api/balance${force ? '?refresh=1' : ''}`)).json();
    pricingState = d.pricing || pricingState;
    paintPhase(d.pricing);
    const available = d.available === true && (d.entries || []).length > 0;
    primary.textContent = available ? (d.display?.label || '余额可用') : '余额暂不可用';
    state.textContent = available ? (d.stale ? '缓存' : '原生账户') : ({ auth_required: '未登录', unsupported: '不支持' }[d.status] || '不可用');
    note.textContent = available ? `更新于 ${formatTime(d.updatedAt)}` : (d.error || '读取失败');
    els.balance.classList.toggle('unavailable', !available);
    els.balance.classList.toggle('stale', Boolean(d.stale));
    const rows = (d.entries || []).map(entry => `
      <tr><td>${esc(currencySymbol(entry.currency))}${esc(entry.currency)}</td><td>${entry.wallet === 'bonus' ? '赠送钱包' : '充值钱包'}</td><td class="amount">${esc(money(entry.amount))}</td></tr>`).join('');
    els.balanceDetails.innerHTML = `
      <strong>DeepSeek Harness 账户余额</strong>
      <p class="balance-source">来源：${esc(d.source || 'harness-account')} · 更新时间：${esc(formatTime(d.updatedAt))}${d.stale ? ' · 数据可能过期' : ''}</p>
      ${rows ? `<table class="balance-table"><thead><tr><th>币种</th><th>钱包</th><th>金额</th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="balance-empty">没有可显示的余额条目。</p>'}
      ${d.pricing ? `<div class="pricing-row"><span class="phase-badge ${esc(d.pricing.phase)}">${esc(d.pricing.label)}</span>
        <span>${esc(d.pricing.localWeekday)} ${esc(d.pricing.localTime)} · ${esc(d.pricing.zone)}<br>
        ${d.pricing.nextSwitch ? `${esc(remainingText(d.pricing.nextSwitchEpoch))}后转为${d.pricing.phase === 'peak' ? '闲时' : '忙时'}（${esc(d.pricing.nextSwitch)}）` : '暂无切换信息'}<br>
        <span class="pricing-note">${esc(d.pricing.note || '')}</span></span></div>` : ''}
      <p class="balance-hint">金额为 Platform 返回的十进制字符串；充值钱包与赠送钱包分开显示，不相加。${d.detail?.note ? ' ' + esc(d.detail.note) : ''}</p>
      <button type="button" id="balance-refresh">刷新余额</button>`;
    els.balanceDetails.querySelector('#balance-refresh').onclick = event => { event.stopPropagation(); loadBalance(true); };
  } catch {
    primary.textContent = '余额暂不可用';
    state.textContent = '读取失败';
    note.textContent = '稍后重试';
    els.balance.classList.add('unavailable');
    els.balanceDetails.innerHTML = '<span>暂时没有可用的余额数据。</span>';
  }
}

els.balance.onclick = event => {
  event.stopPropagation();
  els.balanceDetails.hidden = !els.balanceDetails.hidden;
  els.balance.setAttribute('aria-expanded', String(!els.balanceDetails.hidden));
};
els.balanceDetails.onclick = event => event.stopPropagation();
document.addEventListener('click', () => { els.balanceDetails.hidden = true; els.balance.setAttribute('aria-expanded', 'false'); });

function itemText(i) {
  if (!i) return '';
  if (i.type === 'userMessage') return (i.content || []).filter(x => x.type === 'text').map(x => x.text || '').join('\n');
  if (i.type === 'agentMessage') return i.text || '';
  return '';
}
function reasoningText(i) {
  const s = i.text ?? i.summary ?? i.content ?? '';
  if (Array.isArray(s)) return s.map(x => typeof x === 'string' ? x : (x.text || JSON.stringify(x))).join('\n\n');
  return typeof s === 'string' ? s : JSON.stringify(s, null, 2);
}

function renderItem(item, turn) {
  if (item.type === 'agentMessage') {
    const text = itemText(item);
    return text ? `<div class="commentary markdown-body">${window.renderMarkdown(text)}</div>` : '';
  }
  if (item.type === 'reasoning') {
    const content = reasoningText(item).trim();
    if (!content) return '';
    return `<details class="reasoning-card"><summary><span>推理摘要</span><span class="expand-hint">展开查看</span></summary><div class="reasoning-body markdown-body">${window.renderMarkdown(content)}</div><div class="voice-actions">${window.voiceUI?.record(turn, item, content, "reasoning") || ""}</div></details>`;
  }
  if (item.type === 'todo') {
    const todos = item.todos || [];
    const done = todos.filter(entry => entry.status === 'completed').length;
    if (!todos.length) return '';
    const rows = todos.map(entry => {
      const mark = entry.status === 'completed' ? '✓' : entry.status === 'in_progress' ? '◐' : '○';
      return `<li class="todo-item ${esc(entry.status)}"><span class="todo-mark">${mark}</span><span>${window.renderMarkdown(entry.content)}</span></li>`;
    }).join('');
    return `<details class="todo-card" ${done < todos.length ? 'open' : ''}><summary><span>执行计划</span><span class="todo-progress">${done}/${todos.length} 完成</span></summary><ul class="todo-list">${rows}</ul></details>`;
  }
  if (item.type === 'notice') {
    return `<p class="bridge-notice ${esc(item.level || 'info')}">${esc(item.text || '')}</p>`;
  }
  if (item.type === 'usage') {
    if (!item.totalTokens) return '';
    return `<p class="bridge-usage">本轮用量：输入 ${Number(item.inputTokens || 0).toLocaleString()} · 输出 ${Number(item.outputTokens || 0).toLocaleString()} · 合计 ${Number(item.totalTokens || 0).toLocaleString()}</p>`;
  }
  if (item.type === 'toolCall') {
    const state = item.status === 'running' ? '运行中' : (item.isError ? '失败' : '完成');
    const body = [
      item.arguments ? `参数\n${item.arguments}` : '',
      item.result ? `结果\n${item.result}` : ''
    ].filter(Boolean).join('\n\n');
    return `<details class="tool tool-${esc(item.status || 'completed')}"><summary><span class="tool-name">${esc(item.name)}</span><span class="tool-state">${esc(state)}</span></summary><pre>${esc(body || '（无输出）')}</pre></details>`;
  }
  return '';
}

function renderAttachments(turn) {
  const files = turn.bridgeAttachments || [];
  if (!files.length) return '';
  return `<section class="attachments"><div class="attachment-heading">返回的文件</div>${files.map(file => {
    const path = file.agentDiskPath || file.localPath;
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const src = window.bridgeUrl(`/api/attachment?path=${encodeURIComponent(path)}`);
    const direct = file.delivery !== 'local_only' || true;
    const preview = direct && imageExtensions.has(ext) ? `<a href="${src}" target="_blank" rel="noopener"><img class="attachment-preview" src="${src}" alt="${esc(file.name)}" loading="lazy"></a>` : '';
    return `<div class="attachment-card">${preview}<div class="attachment-info"><strong>${esc(file.name)}</strong><span>本机文件：${esc(path)}</span><span>${esc(file.reason || '')}</span></div>
      <div class="attachment-actions"><a href="${src}" target="_blank" rel="noopener">查看 / 下载</a><button data-open-path="${esc(path)}">本机打开</button></div></div>`;
  }).join('')}</section>`;
}

function bindAttachmentButtons() {
  document.querySelectorAll('[data-open-path]').forEach(b => b.onclick = async () => {
    const original = b.textContent;
    b.disabled = true;
    b.textContent = '正在打开…';
    try {
      const r = await bridgeFetch('/api/open-attachment', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: b.dataset.openPath }) });
      if (!r.ok) throw new Error();
      b.textContent = '已打开';
    } catch { b.textContent = '文件不存在'; }
    setTimeout(() => { b.disabled = false; b.textContent = original; }, 1800);
  });
}

function renderTurns(turns) {
  window.voiceUI?.clear(selected);
  if (!turns.length) return '<div class="empty"><h3>暂无历史</h3><p>在下方输入第一条消息，或在左侧新建会话。</p></div>';
  return turns.map(turn => {
    const items = turn.items || [];
    const user = items.find(x => x.type === 'userMessage');
    const agents = items.filter(x => x.type === 'agentMessage');
    const final = [...agents].reverse().find(x => x.phase !== 'commentary');
    const middle = items.filter(x => x !== user && x !== final).map(item => renderItem(item, turn)).join('');
    const answer = itemText(final) || (agents.length ? itemText(agents.at(-1)) : '');
    const modelLabel = turn.bridgeModel ? `${turn.bridgeModel.model}` : '模型未记录';
    const turnStatus = turn.status === 'in_progress' ? '<span class="turn-running">运行中</span>' : '';
    return `<article class="turn turn-${esc(turn.status || 'unknown')}">
      <div class="user"><div class="label">你</div><div class="bubble">${esc(itemText(user) || '（非文字输入）')}${user?.bridgeImageCount ? `<span class="user-images">＋${user.bridgeImageCount} 张图片</span>` : ''}</div></div>
      ${middle}
      <div class="assistant"><div class="label">DeepSeek Harness · ${esc(modelLabel)}${turnStatus}</div><div class="bubble"><div class="markdown-body">${window.renderMarkdown(answer || (turn.status === 'in_progress' ? '正在处理…' : '（尚无最终回答）'))}</div><div class="voice-actions">${final ? window.voiceUI?.record(turn, final, answer, 'final') || '' : ''}</div></div></div>
      ${renderAttachments(turn)}
    </article>`;
  }).join('');
}

async function loadThread(id, silent = false, showOlder = false) {
  if (silent && threadLoading && selected === id) return;
  const switching = selected !== id;
  if (switching && !threadVisibleCounts.has(id)) threadVisibleCounts.set(id, 6);
  const limit = threadVisibleCounts.get(id) || 6;
  if (threadController) threadController.abort();
  const controller = new AbortController(), requestId = ++threadRequestSerial;
  threadController = controller;
  threadLoading = true;
  selected = id;
  syncUrl(id);
  renderThreads();
  if (!silent) els.messages.innerHTML = '<div class="loading">正在读取最近对话…</div>';
  try {
    const response = await bridgeFetch(`/api/thread/${encodeURIComponent(id)}?turnLimit=${limit}`, { signal: controller.signal, cache: 'no-store' });
    const data = await response.json();
    if (requestId !== threadRequestSerial || selected !== id) return;
    if (data.error) {
      if (!silent) els.messages.innerHTML = `<div class="loading">${esc(data.error)}</div>`;
      return;
    }
    const t = data.thread || {};
    currentThread = t;
    const total = Number(t.bridgeTotalTurns ?? (t.turns || []).length), visible = (t.turns || []).length;
    const signature = JSON.stringify([total, visible, (t.turns || []).map(turn => [turn.id, turn.status, (turn.items || []).length])]);
    const summary = threads.find(item => item.id === id);
    els.title.textContent = summary?.name || t.name || '未命名会话';
    const preset = t.bridgeAgentPreset ? ` · 预设 ${t.bridgeAgentPreset}` : '';
    const plan = Number(t.bridgeTodosTotal) ? ` · 计划 ${Number(t.bridgeTodosDone) || 0}/${Number(t.bridgeTodosTotal)}` : '';
    els.meta.textContent = `项目：${projectName(summary?.cwd || t.cwd)} · ${statusLabel(t.status)} · ${total} 轮${preset}${plan} · ID：${t.id || id}`;
    renderActivity(t);
    syncThreadPermission(id).catch(() => {});
    loadModels(id).catch(() => {});
    if (!silent || signature !== lastThreadSignature) {
      const nearBottom = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight < 140;
      const remaining = Math.max(0, total - visible);
      const older = remaining ? `<div class="older-turns-wrap"><button id="load-older-turns" type="button">查看更早 6 轮（还有 ${remaining} 轮）</button><button id="load-all-turns" type="button">查看全部</button></div>` : '';
      els.messages.innerHTML = older + renderTurns(t.turns || []);
      const olderButton = document.querySelector('#load-older-turns'), allButton = document.querySelector('#load-all-turns');
      if (olderButton) olderButton.onclick = () => { threadVisibleCounts.set(id, Math.min(total, Number(limit) + 6)); loadThread(id, false, true); };
      if (allButton) allButton.onclick = () => { threadVisibleCounts.set(id, 'all'); loadThread(id, false, true); };
      bindAttachmentButtons();
      window.voiceUI?.bind(id);
      if (showOlder) els.messages.scrollTop = 0;
      else if (!silent || nearBottom) els.messages.scrollTop = els.messages.scrollHeight;
      lastThreadSignature = signature;
    }
  } catch (error) {
    if (error.name !== 'AbortError' && !silent) els.messages.innerHTML = '<div class="loading">读取失败，请重试</div>';
  } finally {
    if (requestId === threadRequestSerial) { threadLoading = false; threadController = null; }
  }
}

let activityTimer = null;
let activityStarted = 0;

function describeActivity(activity) {
  if (!activity || !activity.running) return '';
  const parts = [];
  if (activity.turn) parts.push(`第 ${activity.turn} 轮`);
  parts.push(activity.currentTool ? `正在运行 ${activity.currentTool}` : '模型正在思考或整理结果');
  if (activity.tools) parts.push(`已调用工具 ${activity.tools} 次`);
  if (activity.todosTotal) parts.push(`计划 ${activity.todosDone}/${activity.todosTotal}`);
  if (activity.startedAt) {
    const seconds = Math.max(0, Math.round((Date.now() - Number(activity.startedAt)) / 1000));
    const minutes = Math.floor(seconds / 60);
    parts.push(`已用时 ${minutes > 0 ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`}`);
  }
  return parts.join(' · ');
}

function renderActivity(thread) {
  const activity = thread?.bridgeActivity;
  const text = describeActivity(activity);
  if (!text) {
    els.activity.hidden = true;
    els.activity.textContent = '';
    return;
  }
  els.activity.hidden = false;
  els.activity.innerHTML = `<span class="activity-dot"></span><span>${esc(text)}</span>`;
}

async function refreshVisibleThread() {
  if (!selected) return;
  await loadThread(selected, true);
}

function toggleProviderMenu(force) {
  const open = force === undefined ? els.providerMenu.hidden : force;
  els.providerMenu.hidden = !open;
  els.providerToggle.setAttribute('aria-expanded', String(open));
}
els.providerToggle.onclick = event => { event.stopPropagation(); toggleProviderMenu(); };
els.providerMenu.onclick = event => event.stopPropagation();
document.addEventListener('click', () => toggleProviderMenu(false));
document.addEventListener('keydown', event => { if (event.key === 'Escape') toggleProviderMenu(false); });

async function boot() {
  await window.voiceUI?.boot();
  await loadPermissionCatalog();
  await loadModels();
  await loadSwitchTargets();
  await loadThreads(false);
  loadStatus();
  loadBalance(false);
  setInterval(() => loadThreads(false), 20000);
  setInterval(loadStatus, 5000);
  setInterval(() => loadBalance(false), 60000);
  setInterval(() => paintPhase(pricingState), 30000);
  setInterval(refreshVisibleThread, 3000);
  clearInterval(activityTimer);
  activityTimer = setInterval(() => {
    if (currentThread && selected) renderActivity(currentThread);
  }, 1000);
  try {
    const wanted = new URLSearchParams(window.location.search).get('thread');
    if (wanted && threads.some(item => item.id === wanted)) await loadThread(wanted);
  } catch {}
}
async function changeSessionPermission(preset) {
  if (!preset) return;
  const threadId = els.permissionSelect.dataset.thread || selected;
  if (!threadId) {
    showTaskNotice('先选择一个会话，或把它设为新会话默认权限', true);
    return;
  }
  els.permissionSelect.disabled = true;
  try {
    const response = await bridgeFetch('/api/permissions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ threadId, preset })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '切换失败');
    showTaskNotice(`本会话权限已切换为「${permissionLabel(data.current || preset)}」`);
    await loadThread(threadId, true);
  } catch (error) {
    showTaskNotice(`权限切换失败：${error.message}`, true);
  } finally {
    els.permissionSelect.disabled = false;
  }
}

async function changeDefaultPermission(preset) {
  if (!preset) return;
  try {
    const response = await bridgeFetch('/api/permissions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '保存失败');
    permissionCatalog.bridgeDefault = data.bridgeDefault || preset;
    if (els.permissionNote) els.permissionNote.textContent = `新会话将使用「${permissionLabel(preset)}」。`;
    showTaskNotice(`新会话默认权限：${permissionLabel(preset)}`);
  } catch (error) {
    showTaskNotice(`默认权限保存失败：${error.message}`, true);
  }
}

function showTaskNotice(message, error = false) {
  const strip = document.querySelector('#task-strip');
  if (!strip) return;
  strip.hidden = !message;
  strip.textContent = message || '';
  strip.className = `task-strip${error ? ' error' : ''}`;
  if (message && !error) {
    clearTimeout(showTaskNotice.timer);
    showTaskNotice.timer = setTimeout(() => { strip.hidden = true; }, 4000);
  }
}

els.permissionSelect.onchange = () => changeSessionPermission(els.permissionSelect.value);

if (els.modelChip) {
  els.modelChip.onclick = event => {
    event.stopPropagation();
    const open = els.modelMenu.hidden;
    els.modelMenu.hidden = !open;
    els.modelChip.setAttribute('aria-expanded', String(open));
    if (open) paintModelMenu();
  };
  els.modelMenu.onclick = event => event.stopPropagation();
  document.addEventListener('click', () => {
    if (!els.modelMenu.hidden) {
      els.modelMenu.hidden = true;
      els.modelChip.setAttribute('aria-expanded', 'false');
    }
  });
}
if (els.defaultPermission) els.defaultPermission.onchange = () => changeDefaultPermission(els.defaultPermission.value);

els.settings.onclick = () => {
  window.voiceUI?.refreshSettings();
  loadPermissionCatalog();
  els.settingsDialog.showModal();
};
els.closeSettings.onclick = () => els.settingsDialog.close();
els.settingsDone.onclick = () => els.settingsDialog.close();
els.menu.onclick = () => document.body.classList.add('sidebar-open');
els.backdrop.onclick = () => document.body.classList.remove('sidebar-open');
els.search.oninput = renderThreads;
els.refresh.onclick = () => { loadThreads(); if (selected) loadThread(selected, true); loadBalance(true); };
els.loadMore.onclick = () => loadThreads(true);
boot();
