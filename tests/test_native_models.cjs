// SPDX-License-Identifier: MIT
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {providerOverlay} = require('../adapter/provider-profile.cjs');
const parse = {parse: JSON.parse};
const rows = [
  {id:'agent-default-model',config:{provider:'example',model:'a'}},
  {insert:[{id:'llm-pi-ai',config:{providers:{example:{apiKeyEnv:'EXAMPLE_KEY',models:[{id:'a'}]}}}}]},
  {id:'desktop-ui',config:{secret:'desktop-only'}},
  {id:'llm-pi-ai',disabled:true,config:{providers:{disabled:{}}}},
];
const overlay = providerOverlay(parse, JSON.stringify(rows));
assert.deepEqual(overlay,[{id:'llm-pi-ai',config:{providers:{example:{apiKeyEnv:'EXAMPLE_KEY',models:[{id:'a'}]}}}}]);
assert.ok(!JSON.stringify(overlay).includes('desktop-only'));
const source = fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const chips = {modelChip:{disabled:false},effortChip:{disabled:false}};
const requests = [];
const context = vm.createContext({els:chips,selected:'session-a',modelSelection:{},
  modelCatalog:{models:[{id:'example::a',efforts:[{id:'medium'},{id:'xhigh'}],defaultEffort:'medium'}],efforts:[{id:'max'}]},
  bridgeFetch:async (url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({})}},
  paintModelChip(){},paintModelMenu(){},modelLabels:()=>({}),showTaskNotice(){},loadThread:async()=>{},
});
vm.runInContext(source.slice(source.indexOf('function modelEfforts('),source.indexOf('function modelLabels(')),context);
vm.runInContext(source.slice(source.indexOf('async function applyModel('),source.indexOf('function projectName(')),context);
(async()=>{
  await vm.runInContext("applyModel('example::a','max')",context);
  assert.equal(requests[0].effort,'medium');
  assert.equal(requests[0].model,'example::a');
  await vm.runInContext("applyModel('example::a','xhigh')",context);
  assert.equal(requests[1].effort,'xhigh');
  chips.modelChip.disabled = true;
  await vm.runInContext("applyModel('example::a','medium')",context);
  assert.equal(requests.length,2);
  console.log('Provider inheritance and native model/effort selection passed');
})().catch(error=>{console.error(error);process.exitCode=1});
