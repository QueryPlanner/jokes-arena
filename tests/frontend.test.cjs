// Exercise the public UI events and fetch contract without new dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {
    this.textContent = ''; this.hidden = false; this.disabled = false;
    this.children = []; this.attributes = {}; this.events = {};
    this.classes = new Set();
    this.classList = {
      add: value => this.classes.add(value), remove: value => this.classes.delete(value),
      toggle: (value, on) => on ? this.classes.add(value) : this.classes.delete(value)
    };
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(name, action) { this.events[name] = action; }
  click() { return this.events.click(); }
  focus(options) { this.focused = true; this.focusOptions = options; }
}
const pair = (id, word) => ({id, word, jokes: [`${word} first`, `${word} second`]});
const ok = data => ({ok: true, json: async () => data});
const failure = (status, message) => ({ok: false, status, json: async () => message ? {error:message} : {}});
const settle = () => new Promise(resolve => setImmediate(resolve));
function app(responses) {
  const elements = new Map();
  const html = readFileSync('preview/index.html', 'utf8');
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  const requests = [];
  const context = vm.createContext({
    document: {getElementById: id => {
      assert.ok(elements.has(id), `Unknown HTML element ${id}`);
      return elements.get(id);
    }, createElement: () => new Element()},
    fetch: async (path, options) => {
      requests.push({path, options});
      assert.ok(responses.length, `Unexpected request ${path}`);
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return response;
    }
  });
  vm.runInContext(readFileSync('preview/app.js', 'utf8'), context, {filename:'preview/app.js'});
  return {elements, requests, context};
}

test('card saves once, reveals models, and waits for Next pair', async () => {
  const responses = [ok(pair('one', 'coffee')), ok({saved:true,models:['Model Left','Model Right']}), ok(pair('two', 'umbrella'))];
  const {elements:e, requests} = app(responses);
  await settle();
  assert.equal(e.get('word').textContent, 'coffee');
  assert.equal(e.get('left-card').disabled, false);
  const pending = e.get('left-card').click();
  e.get('right-card').click();
  assert.equal(e.get('right-card').disabled, true);
  await pending;
  assert.equal(requests.length, 2);
  assert.equal(e.get('word').textContent, 'coffee');
  assert.equal(e.get('left-model').textContent, 'Model Left');
  assert.equal(e.get('right-model').textContent, 'Model Right');
  assert.equal(e.get('left-model').hidden, false);
  assert.equal(e.get('left-action').textContent, 'Your pick');
  assert.equal(e.get('right-action').textContent, 'Other joke');
  assert.equal(e.get('next-pair').focused, true);
  assert.equal(e.get('next-pair').focusOptions.preventScroll, true);
  e.get('left-card').click();
  assert.equal(requests.length, 2);
  await e.get('next-pair').click();
  assert.equal(requests.length, 3);
  assert.equal(e.get('left-model').hidden, true);
  assert.equal(e.get('left-model').textContent, '');
  assert.equal(e.get('next-pair').hidden, true);
  assert.equal(e.get('left-card').focused, true);
  assert.equal(requests[1].path, '/api/battles/one/vote');
  assert.deepEqual(JSON.parse(requests[1].options.body), {choice:'left'});
  assert.equal(e.get('word').textContent, 'umbrella');
  assert.equal(e.get('left-card').classes.has('selected'), false);
});

test('failed vote retains the pair and retries the same choice', async () => {
  const {elements:e, requests} = app([ok(pair('one', 'coffee')), failure(503, 'Unavailable'), ok({saved:true,models:['Model Left','Model Right']}), ok(pair('two', 'calendar'))]);
  await settle();
  await e.get('right-card').click();
  assert.equal(e.get('word').textContent, 'coffee');
  assert.equal(e.get('retry').hidden, false);
  assert.equal(e.get('left-model').hidden, true);
  assert.equal(e.get('next-pair').hidden, true);
  assert.match(e.get('battle-status').textContent, /could not be confirmed/);
  e.get('left-card').click();
  assert.equal(requests.length, 2);
  assert.equal(e.get('left-card').disabled, true);
  await e.get('retry').click();
  assert.equal(requests[1].path, requests[2].path);
  assert.deepEqual(JSON.parse(requests[2].options.body), {choice:'right'});
  assert.equal(e.get('right-action').textContent, 'Your pick');
  await e.get('next-pair').click();
  assert.equal(e.get('word').textContent, 'calendar');
});

test('a saved vote with failed next fetch retries the fetch without resubmitting', async () => {
  const {elements:e, requests} = app([ok(pair('one', 'coffee')), ok({saved:true,models:['Model Left','Model Right']}), new Error('Offline'), ok(pair('two', 'calendar'))]);
  await settle();
  await e.get('left-card').click();
  await e.get('next-pair').click();
  assert.equal(e.get('left-model').textContent, 'Model Left');
  assert.equal(e.get('left-model').hidden, false);
  assert.equal(e.get('next-pair').hidden, true);
  assert.match(e.get('battle-status').textContent, /comparison has a saved vote/);
  assert.equal(e.get('left-card').disabled, true);
  e.get('left-card').click();
  await e.get('retry').click();
  assert.equal(requests.filter(request => request.path.endsWith('/vote')).length, 1);
  assert.equal(e.get('word').textContent, 'calendar');
});

test('initial load failure recovers and an exhausted pool offers results', async () => {
  const results = {total_votes:0, pool_pairs:0, models:[]};
  const {elements:e, requests} = app([failure(503), failure(409, 'Exhausted'), ok(results)]);
  await settle();
  assert.match(e.get('battle-status').textContent, /request failed/);
  assert.equal(e.get('left-card').disabled, true);
  await e.get('retry').click();
  assert.equal(e.get('see-results').hidden, false);
  assert.equal(e.get('cards').hidden, true);
  assert.equal(e.get('round-info').hidden, true);
  e.get('left-card').click();
  assert.equal(requests.length, 2);
  e.get('see-results').click();
  await settle();
  assert.equal(e.get('battle-view').hidden, true);
  assert.equal(e.get('leaderboard-view').hidden, false);
  assert.match(e.get('leaderboard-status').textContent, /No votes yet/);
});

test('leaderboard renders rates, shared ranks, zero-vote state, and refresh failures', async () => {
  const model = (name, wins, losses, rate) => ({name,wins,losses,win_rate:rate,comparisons:wins+losses,unique_jokes:3});
  const {elements:e, requests} = app([ok(pair('one','coffee')), ok({total_votes:2,pool_pairs:3,models:[model('Model One',1,1,.5),model('Model Two',1,1,.5),model('<script>',0,0,null)]}), failure(503,'Offline'), ok({total_votes:0,pool_pairs:3,models:[]})]);
  await settle();
  e.get('leaderboard-tab').click();
  await settle();
  assert.equal(e.get('vote-count').textContent, 2);
  const rows = e.get('rankings').children.map(row => row.children.map(cell => cell.textContent));
  assert.deepEqual(rows, [[1,'Model One','Default','50.0%',1,1,2,3],[1,'Model Two','Default','50.0%',1,1,2,3],['—','<script>','Default','No votes',0,0,0,3]]);
  assert.equal(requests[1].options.method, undefined);
  assert.equal(e.get('leaderboard-tab').attributes['aria-current'], 'page');
  await e.get('refresh-results').click();
  assert.match(e.get('leaderboard-status').textContent, /Could not load results/);
  assert.equal(e.get('refresh-results').disabled, false);
  await e.get('refresh-results').click();
  assert.equal(e.get('rankings').children.length, 0);
  e.get('battle-tab').click();
  assert.equal(e.get('battle-view').hidden, false);
  assert.equal(e.get('leaderboard-view').hidden, true);
  assert.equal(e.get('leaderboard-tab').attributes['aria-current'], undefined);
});

test('a stale-tab conflicting vote moves to the next pair without resubmission', async () => {
  const {elements:e, requests} = app([ok(pair('one', 'coffee')), failure(409, 'A different vote is saved'), ok(pair('two', 'calendar'))]);
  await settle();
  await e.get('right-card').click();
  assert.equal(e.get('word').textContent, 'calendar');
  assert.match(e.get('battle-status').textContent, /already has a saved vote/);
  assert.equal(e.get('retry').hidden, true);
  assert.equal(e.get('left-card').disabled, false);
  assert.equal(requests.filter(request => request.path.endsWith('/vote')).length, 1);
});

test('word bucket, actual reasoning settings, and unavailable models are visible', async () => {
  const results={total_votes:0,pool_pairs:24,word_bucket:['airport','coffee'],
    unavailable_models:['Gemini 4 Argon','K2 Horizon'],model_notes:['Muse Spark 1.3 selected.'],
    models:[{name:'GPT-6 Astra',reasoning_label:'low',win_rate:null,wins:0,losses:0,comparisons:0,unique_jokes:2}]};
  const {elements:e}=app([ok(pair('one','coffee')),ok(results)]);
  await settle();
  e.get('leaderboard-tab').click();
  await settle();
  assert.equal(e.get('word-count').textContent,2);
  assert.equal(e.get('word-list').textContent,'airport, coffee');
  assert.match(e.get('unavailable-models').textContent,/Gemini 4 Argon, K2 Horizon/);
  assert.equal(e.get('model-notes').textContent,'Muse Spark 1.3 selected.');
  assert.equal(e.get('rankings').children[0].children[2].textContent,'low');
});
