const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
(async () => {
  const calls = [], redirects = [], order = [];
  const location = {hostname: 'bridge.example.test', pathname: '/dsh/', search: '?provider=deepseek-harness',
    assign: url => {order.push('redirect'); redirects.push(url);}};
  const window = {DSH_BRIDGE: {basePath: '/dsh/'}, bridgeSaveDraft: async () => order.push('saved')};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/bridge-client.js'), 'utf8'),
    {window, location, navigator: {}, Request, fetch: async (url, init) => {calls.push({url, init});return {status: 401};}});
  await window.bridgeFetch('/api/threads');
  await window.bridgeFetch('/api/interactions');
  assert.equal(calls[0].url, '/dsh/api/threads');
  assert.deepEqual(order, ['saved', 'redirect']);
  assert.equal(redirects.length, 1);
  assert.equal(redirects[0], '/signed-out.html');
  console.log('Base path and draft-preserving, single signed-out redirect passed');
})().catch(error => {console.error(error);process.exitCode = 1;});
