const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/local-composer.js'), 'utf8');
const polling = source.slice(source.indexOf('const localTrackedTasks'), source.indexOf('setInterval(renderLocalTracked'));

async function replay(responses, turns, {refreshFails = false} = {}) {
  let calls = 0, refreshes = 0, message = '';
  const context = vm.createContext({
    selected: 'session', currentThread: {id: 'session', turns}, localTaskStrip: {},
    document: {hidden: false}, setTimeout: callback => callback(),
    localShowTask: text => { message = text; },
    responseJson: response => response.data,
    bridgeFetch: async url => {
      assert.equal(url, '/api/local-task/task');
      const data = responses[calls++];
      if (!data) throw new Error('unexpected poll');
      if (data instanceof Error) throw data;
      return {ok: !data.code, status: data.code ? 404 : 200, data};
    },
    loadThread: async () => { refreshes++; if (refreshFails) throw new Error('snapshot unavailable'); },
  });
  // Fail a runaway retry without needing a real timer.
  context.setTimeout = callback => {
    if (calls >= responses.length) throw new Error('polling continued after the last response');
    callback();
  };
  await vm.runInContext(polling + "\npollLocalTask('task', 'session')", context);
  assert.equal(calls, responses.length);
  return {message, refreshes};
}

(async () => {
  const running = {status:'running', message:'正在处理', threadId:'session'};
  const done = {status:'completed', message:'任务已完成', turnId:'turn-12', threadId:'session'};
  const stale = [{id:'turn-12', status:'in_progress'}];
  const long = [...Array(1802).fill(running), new Error('temporarily offline'), done];
  assert.deepEqual(await replay(long, stale), {message:'任务已完成', refreshes:1});
  assert.equal((await replay([{...done, steered:true}], [], {refreshFails:true})).message, done.message);
  assert.equal((await replay([done], [...stale, {id:'turn-13',status:'failed'}])).message, done.message);
  const interrupted = {...done, status:'interrupted', message:'本轮已中断'};
  const history = [{id:'turn-12',status:'interrupted',items:[{type:'agentMessage',phase:'final'}]}];
  assert.match((await replay([interrupted], history)).message, /已有回复/);
  assert.match((await replay([{code:'task_not_found'}], history)).message, /不再保留/);
  console.log('PASS long-running tasks, stale snapshots, steering, interruptions and expired tasks');
})().catch(error => { console.error(error); process.exitCode = 1; });
