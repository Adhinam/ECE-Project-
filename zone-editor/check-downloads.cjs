// Focused browser-logic checks; no video inference or browser automation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const storage = new Map();
let downloaded = 0, savedUrl;
const context = vm.createContext({
  $: id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
  location: {protocol:'file:', href:'http://127.0.0.1:8766/'},
  // The editor already owns this name; run persistence must use window.history.
  history: [], window: {history:{replaceState: (_state,_title,url) => { savedUrl=String(url); }}},
  localStorage: {setItem:(key,value)=>storage.set(key,value),getItem:key=>storage.get(key)},
  URL: class extends URL {static createObjectURL(){return 'blob:example';} static revokeObjectURL(){}},
  document: {createElement:()=>({click:()=>downloaded++,remove(){}}),body:{append(){}}},
  setTimeout:()=>0,
  fetch:async()=>({ok:false,json:async()=>({detail:'File unavailable'})}),
});
vm.runInContext(fs.readFileSync(__dirname+'/processing.js','utf8'),context);
(async()=>{
  vm.runInContext("rememberRun('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'); dashboardReport={entries:3};",context);
  assert.match(savedUrl,/\?run=a{32}$/);
  assert.equal(vm.runInContext('lastRun()',context),'a'.repeat(32));
  assert.equal(vm.runInContext('clock(59.99)',context),'1:00.0');
  const button={disabled:false};context.downloadButton=button;
  await vm.runInContext("downloadReport(currentRun,'queue_totals.csv',downloadButton)",context);
  assert.match(elements.get('downloadStatus').textContent,/Download failed.*results are still saved/);
  assert.equal(button.disabled,false);
  assert.equal(downloaded,0);
  assert.equal(vm.runInContext('dashboardReport.entries',context),3);
  context.fetch=async()=>({ok:true,blob:async()=>({})});
  await vm.runInContext("downloadReport(currentRun,'queue_totals.csv',downloadButton)",context);
  assert.equal(downloaded,1);
  assert.equal(button.disabled,false);
  assert.equal(vm.runInContext('dashboardReport.entries',context),3);
  assert.match(elements.get('downloadStatus').textContent,/results remain saved/);
  console.log('Passed: run persistence, timestamp rounding, failed download recovery, successful download retains results.');
})().catch(error=>{console.error(error);process.exitCode=1;});
