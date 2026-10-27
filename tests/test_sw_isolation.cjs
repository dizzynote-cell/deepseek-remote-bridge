/* Offline regression: a mounted bridge owns only its own scope's cache. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
(async () => {
  const handlers = {}, deleted = [], opened = [];
  const scope = 'https://bridge.example.test/dsh/';
  const own = 'dsb-shell:' + scope + ':';
  const self = {registration: {scope}, location: {origin: 'https://bridge.example.test'},
    clients: {claim: async () => {}}, addEventListener: (name, fn) => handlers[name] = fn};
  const cache = {match: async () => ({cached: true}), put: async () => {}};
  const caches = {keys: async () => [own + 'v1', own + 'v2', own + 'v3', own + 'v4', 'codex-shell-v1', 'other-app', 'dsb-shell:https://bridge.example.test/other/:v1'],
    delete: async name => deleted.push(name), open: async name => {opened.push(name); return cache;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/sw.js'), 'utf8'),
    {self, caches, URL, fetch: async () => ({ok: false})});
  let activation;
  handlers.activate({waitUntil: promise => activation = promise});
  await activation;
  assert.deepEqual(deleted, [own + 'v1', own + 'v2', own + 'v3']);
  for (const url of ['https://bridge.example.test/app.js', scope + 'api/threads', 'https://other.test/dsh/styles.css']) {
    handlers.fetch({request: {url, method: 'GET'}, respondWith: () => assert.fail('intercepted another app or an API')});
  }
  let response;
  handlers.fetch({request: {url: scope + 'styles.css', method: 'GET'}, respondWith: promise => response = promise});
  assert.equal((await response).cached, true);
  assert.ok(opened.every(name => name === own + 'v4'));
  console.log('Service-worker scope and cache isolation passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
