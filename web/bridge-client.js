/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
 * Base-path aware fetch wrapper. The page can be mounted at / or under a
 * prefix such as /dsh/; every request resolves against the injected base.
 */
(() => {
  const boot = window.DSH_BRIDGE || {};
  const base = typeof boot.basePath === 'string' && boot.basePath ? boot.basePath : '/';
  const normalized = base.endsWith('/') ? base : base + '/';
  window.DSH_BRIDGE = Object.assign({ basePath: normalized }, boot);
  window.bridgeUrl = path => {
    const value = String(path ?? '');
    if (/^[a-z]+:/i.test(value) || value.startsWith('//')) return value;
    return normalized + value.replace(/^\/+/, '');
  };
  window.bridgeFetch = (input, init) => {
    if (typeof input === 'string') return fetch(window.bridgeUrl(input), init);
    if (input instanceof Request) return fetch(new Request(window.bridgeUrl(input.url), input), init);
    return fetch(input, init);
  };
  window.bridgeEscape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
})();
