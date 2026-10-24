/* SPDX-License-Identifier: MIT — Project: DeepSeek Remote Bridge
 * Shell cache for the installed (home-screen) app.
 * Only static assets are cached; every /api/ request and every HTML navigation
 * always goes to the network so the page never shows a stale bridge state.
 */
const CACHE = 'dsb-shell-v1';
const SHELL = [
  'styles.css',
  'mode.css',
  'scrollbars.css',
  'attachments.css',
  'projects.css',
  'thread-browser.css',
  'mobile.css',
  'local-composer.css',
  'local-polish.css',
  'balance.css',
  'markdown.css',
  'interactions.css',
  'provider-menu.css',
  'voice.css',
  'bridge-client.js',
  'markdown.js',
  'project-display.js',
  'voice-ui.js',
  'app.js',
  'local-composer.js',
  'interactions.js',
  'lib/markdown-it-15.0.3.min.js',
  'favicon.svg'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL.map(path => new URL(path, self.registration.scope).href)))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.includes('/api/')) return;
  if (request.mode === 'navigate') return;
  // Stale-while-revalidate: the cached copy answers immediately, and the fresh
  // response updates the cache for the next load. Without this, an installed app
  // would keep old CSS/JS until the cache name changed by hand.
  event.respondWith(
    caches.match(request).then(hit => {
      const refresh = fetch(request).then(response => {
        if (response.ok && response.type === 'basic') {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, copy)).catch(() => {});
        }
        return response;
      });
      if (hit) {
        refresh.catch(() => {});
        return hit;
      }
      return refresh;
    })
  );
});
