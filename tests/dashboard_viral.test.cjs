// Browser event logic exercised with a fake DOM and mocked fetch only.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(fetchResponse) {
  const calls = [];
  const mode = {value:'messi_ronaldo', addEventListener:(_, fn) => { mode.change = fn; }};
  const language = {value:'auto', options:[{textContent:'Auto'}]};
  const form = {elements:{video_mode:mode, language, csrf:{value:'mock-csrf'},
    trend_id:{value:''}, trend_snapshot:{value:''}}};
  const panel = {hidden:true, children:[], textContent:'', append(node) { this.children.push(node); },
    replaceChildren() { this.children = []; }};
  const document = {addEventListener() {}, createElement:() => ({textContent:''}),
    getElementById:id => ({'new-video-form':form,'viral-recommendation':panel}[id] || null)};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/elsewhere/static/dashboard.js'), 'utf8'), {
    document, FormData, window:{setTimeout:() => assert.fail('No polling on New Video'), addEventListener() {}},
    fetch:async (url, options) => {
      calls.push(url);
      assert.equal(url, '/api/trends/recommendation');
      assert.equal(options.body.get('csrf'), 'mock-csrf');
      return {ok:true, json:async () => fetchResponse};
    }
  });
  return {calls, mode, language, form, panel};
}

test('Viral change auto-selects one topic and language, without any paid endpoint', async () => {
  const s = setup({snapshot_id:'snapshot', topic:{id:'topic', title:'<img src=x onerror=bad()>',
    regions_seen:2, momentum_estimate:20000, ranking_reason:'Estimate only', language_reason:'Topic context',
    youtube_status:'Not checked'}, language_name:'Hindi', sources_responded:50, sources_attempted:58});
  assert.equal(s.calls.length, 0);
  s.mode.value = 'viral';
  await s.mode.change();
  assert.equal(s.calls.length, 1);
  assert.equal(s.form.elements.trend_id.value, 'topic');
  assert.equal(s.form.elements.trend_snapshot.value, 'snapshot');
  assert.match(s.language.options[0].textContent, /Hindi/);
  assert.equal(s.panel.children[0].textContent, 'Auto-selected: <img src=x onerror=bad()>');
  s.mode.value = 'messi_ronaldo';
  await s.mode.change();
  assert.equal(s.panel.hidden, true);
  assert.equal(s.form.elements.trend_id.value, '');
  assert.equal(s.calls.length, 1);
});

test('Empty discovery leaves no hidden topic or generation request', async () => {
  const s = setup({topic:null});
  s.mode.value = 'viral';
  await s.mode.change();
  assert.equal(s.form.elements.trend_id.value, '');
  assert.match(s.panel.textContent, /No suitable recent topic/);
  assert.equal(s.calls.length, 1);
});
