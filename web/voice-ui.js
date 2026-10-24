// SPDX-License-Identifier: MIT
// Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
// On-demand speech for completed turns. The provider key never reaches the
// browser: the page asks the bridge to synthesize, then plays the cached MP3.
(() => {
  const records = new Map();
  const jobs = new Map();
  let settings = { enabled: false, configured: false, shown: false, locale: 'zh-CN' };
  let preferences = { voiceEnabled: true };
  let thread = '';
  let audio = null;
  let dock = null;
  let dockLabel = null;
  let dockMessage = null;
  let pollTimer = null;

  const esc = window.bridgeEscape;
  const hash = value => {
    let result = 0x811c9dc5;
    for (let index = 0; index < value.length; index += 1) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 0x01000193) >>> 0;
    }
    return result.toString(16).padStart(8, '0');
  };
  const explain = code => ({
    voice_not_configured: '本机语音尚未配置',
    voice_text_empty: '没有可朗读的文字',
    voice_text_too_long: '文字过长，无法一次合成',
    voice_provider_error: '语音服务返回错误',
    voice_provider_unavailable: '语音服务暂时不可用',
    voice_disabled: '网页语音朗读已关闭',
    voice_audio_too_large: '合成音频过大'
  })[code] || code || '语音请求失败';

  async function json(url, options) {
    const response = await bridgeFetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({ error: '服务返回格式错误' }));
    if (!response.ok) throw Object.assign(new Error(explain(data.code || data.error)), { code: data.code });
    return data;
  }

  async function digest(text) {
    const payload = JSON.stringify([text, settings.voice || '', settings.resource || '', 'v3-sse-mp3-rate25']);
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
    return [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, '0')).join('');
  }

  const allowed = () => Boolean(settings.enabled && settings.configured && preferences.voiceEnabled);

  function record(turn, item, text, kind) {
    const content = String(text || '').trim();
    if (!allowed() || !content || turn.status !== 'completed') return '';
    const ref = `${turn.id}:${kind}:${content.length}:${hash(content)}`;
    records.set(ref, { text: content, threadId: thread, kind, key: null });
    return `<button class="voice-button" type="button" data-voice-ref="${esc(ref)}" aria-label="朗读${kind === 'final' ? '最终回复' : '推理摘要'}" title="生成或播放语音">🔈</button>`;
  }

  function paint(ref, state, message = '') {
    const button = [...document.querySelectorAll('.voice-button')].find(node => node.dataset.voiceRef === ref);
    if (!button) return;
    const waiting = ['requesting', 'queued', 'running'].includes(state);
    button.classList.toggle('cached', state === 'cached');
    button.classList.toggle('busy', waiting);
    button.classList.toggle('voice-failed', state === 'failed');
    button.disabled = waiting;
    button.title = message || (state === 'cached' ? '已有语音，点击播放' : waiting ? '正在合成语音' : '点击生成语音');
    button.textContent = waiting ? '◌' : state === 'cached' ? '🔊' : state === 'failed' ? '!' : '🔈';
    const wrapper = button.closest('.voice-actions') || button.parentElement;
    if (!wrapper) return;
    let feedback = wrapper.querySelector('.voice-feedback');
    if (!feedback) {
      feedback = document.createElement('span');
      feedback.className = 'voice-feedback';
      feedback.setAttribute('role', 'status');
      wrapper.append(feedback);
    }
    feedback.textContent = waiting ? (message || '正在合成语音…') : state === 'failed' ? message : '';
    feedback.hidden = !feedback.textContent;
  }

  function ensureDock() {
    if (dock) return dock;
    dock = document.createElement('section');
    dock.id = 'voice-dock';
    dock.className = 'voice-dock';
    dock.hidden = true;
    dock.setAttribute('aria-label', '语音播放器');
    dockLabel = document.createElement('strong');
    dockLabel.className = 'voice-dock-label';
    dockMessage = document.createElement('span');
    dockMessage.className = 'voice-dock-message';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'voice-dock-close';
    close.textContent = '关闭';
    close.setAttribute('aria-label', '关闭语音播放器');
    close.onclick = closePlayer;
    dock.append(dockLabel, dockMessage, close);
    const messages = document.querySelector('#messages');
    if (messages) messages.after(dock); else document.body.append(dock);
    return dock;
  }

  function closePlayer() {
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.remove();
      audio = null;
    }
    if (dock) {
      dockMessage.textContent = '';
      dock.hidden = true;
    }
  }

  function play(ref, key) {
    closePlayer();
    const item = records.get(ref);
    const button = [...document.querySelectorAll('.voice-button')].find(node => node.dataset.voiceRef === ref);
    if (!button || !item) return;
    ensureDock();
    dockLabel.textContent = item.kind === 'final' ? '最终回复语音' : '推理摘要语音';
    dockMessage.textContent = '';
    dock.hidden = false;
    const player = document.createElement('audio');
    player.src = window.bridgeUrl(`/api/voice/audio/${encodeURIComponent(key)}`);
    player.preload = 'auto';
    player.controls = true;
    player.className = 'voice-player';
    dock.insertBefore(player, dockMessage);
    audio = player;
    player.onerror = () => {
      paint(ref, 'failed', '语音文件无法播放或已被清理，请重新生成');
      dockMessage.textContent = '播放失败。';
      player.remove();
      audio = null;
    };
    player.onended = closePlayer;
    player.play().catch(() => { dockMessage.textContent = '浏览器阻止自动播放，请点播放器的播放键。'; });
  }

  async function click(ref) {
    const item = records.get(ref);
    if (!item) return;
    const job = jobs.get(ref) || {};
    if (job.status === 'cached') { play(ref, job.key || item.key); return; }
    if (['requesting', 'queued', 'running'].includes(job.status)) return;
    jobs.set(ref, { status: 'requesting' });
    paint(ref, 'requesting', '正在合成语音…');
    try {
      const response = await json('/api/voice/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: item.text })
      });
      if (!response.key) throw new Error('语音任务没有返回缓存键');
      item.key = response.key;
      jobs.set(ref, { status: 'cached', key: response.key });
      paint(ref, 'cached');
      play(ref, response.key);
    } catch (error) {
      jobs.set(ref, { status: 'failed' });
      paint(ref, 'failed', error.message || '语音请求失败');
    }
  }

  async function bind(id) {
    thread = id;
    if (!allowed()) return;
    const entries = [...records.entries()].filter(([, item]) => item.threadId === id);
    if (!entries.length) return;
    await Promise.all(entries.map(async ([ref, item]) => {
      if (!item.key) item.key = await digest(item.text);
    }));
    try {
      const keys = [...new Set(entries.map(([, item]) => item.key).filter(Boolean))];
      if (keys.length) {
        const data = await json(`/api/voice/status?keys=${encodeURIComponent(keys.join(','))}`);
        for (const [ref, item] of entries) {
          if (data.cached && data.cached[item.key]) jobs.set(ref, { status: 'cached', key: item.key });
        }
      }
    } catch { /* status is best-effort; clicking still works */ }
    for (const [ref] of entries) {
      const button = [...document.querySelectorAll('.voice-button')].find(node => node.dataset.voiceRef === ref);
      if (button) button.onclick = () => click(ref);
      const job = jobs.get(ref);
      if (job) paint(ref, job.status);
    }
  }

  function clear(id) {
    closePlayer();
    thread = id;
    records.clear();
  }

  async function refreshSettings() {
    try {
      const config = await json('/api/voice/config');
      settings = { ...settings, ...config };
      preferences = { voiceEnabled: config.preferences?.voiceEnabled !== false };
    } catch { /* keep last known settings */ }
    const toggle = document.querySelector('#voice-toggle');
    const note = document.querySelector('#voice-note');
    if (toggle) {
      toggle.checked = preferences.voiceEnabled;
      toggle.disabled = !settings.available && settings.enabled === false ? true : false;
    }
    if (note) {
      note.textContent = !settings.enabled
        ? '本机语音未启用（DSH_BRIDGE_VOICE=0）。'
        : !settings.configured
          ? '本机语音未配置：在 config/voiceapi.env 或环境变量里填 TTS 密钥。'
          : `TTS 已配置（音色 ${settings.voice || '未填'}）。生成的音频缓存在桥自己的目录。`;
    }
    return settings;
  }

  async function boot() {
    await refreshSettings();
    const toggle = document.querySelector('#voice-toggle');
    if (toggle) {
      toggle.onchange = async event => {
        const enabled = event.target.checked;
        try {
          const result = await json('/api/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voiceEnabled: enabled })
          });
          preferences = { voiceEnabled: result.voiceEnabled !== false };
          window.location.reload();
        } catch (error) {
          event.target.checked = !enabled;
          alert(`设置保存失败：${error.message}`);
        }
      };
    }
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (document.querySelector('#settings-dialog')?.open) refreshSettings();
    }, 15000);
  }

  window.voiceUI = { record, clear, bind, boot, refreshSettings };
})();
