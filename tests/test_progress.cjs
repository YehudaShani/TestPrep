// Run with: node --test tests/test_progress.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function viewer() {
  const elements = new Map();
  const storageWrites = [];
  const listeners = {};
  const element = () => ({
    hidden: false, disabled: false, children: [], options: [], dataset: {},
    open: false, value: '', textContent: '', style: {},
    addEventListener() {}, setAttribute() {}, focus() {}, reset() {},
    showModal() { this.open = true; }, close() { this.open = false; },
    classList: { toggle() {}, contains() { return false; } },
    appendChild(child) { this.children.push(child); },
  });
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element, addEventListener() {}, head: element(), body: element(),
  };
  const context = vm.createContext({
    document, location: { hash: '', href: 'http://127.0.0.1:8765/', pathname: '/' },
    localStorage: { getItem() { return '{"old":{"1":{"done":true}}}'; },
      setItem(...args) { storageWrites.push(args); } },
    history: { replaceState() {} }, navigator: {}, URL, setTimeout,
    Image: function () {}, console,
  });
  context.window = context;
  context.addEventListener = (name, handler) => { listeners[name] = handler; };
  const html = fs.readFileSync('src/pdf_splitter/study.html', 'utf8');
  for (const match of html.matchAll(/id="([^"]+)"/g)) document.getElementById(match[1]);
  let script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  script = script.slice(0, script.lastIndexOf('\ninitializeAuth()'));
  vm.runInContext(script + `
    globalThis.api = { setAccount, fetchProgress, retryWrites, saveMark,
      setClient(value) { client = value; },
      prepare(marks) {
        progress = marks; progressCache.circuits = marks;
        state = { course: 'circuits', exam: 'exam', n: 1, mode: 'q' };
        exams = [{ dir: 'exam', questions: [1], answers: [] }];
        enableMarks(true);
      },
      get progress() { return progress; },
      get pending() { return pendingWrites.size; },
      async drain() { await writeQueue; },
    };
  `, context);
  return { api: context.api, elements, storageWrites, listeners };
}

function database({ fail = false, rows = [] } = {}) {
  const writes = [];
  const ranges = [];
  const db = { fail, writes, ranges, from() {
    return {
      async upsert(item) { writes.push({ ...item }); return { error: db.fail ? new Error('offline') : null }; },
      select() { return this; }, eq() { return this; }, order() { return this; },
      async range(start, end) { ranges.push([start, end]); return { data: rows.slice(start, end + 1), error: null }; },
    };
  } };
  return db;
}

test('guest marks stay in memory and old browser marks are ignored', async () => {
  const { api, elements, storageWrites } = viewer();
  await api.setAccount(null);
  const marks = await api.fetchProgress('circuits');
  assert.equal(Object.keys(marks).length, 0);
  api.prepare(marks);
  api.saveMark(true, true);
  assert.equal(api.progress.exam[1].done, true);
  assert.match(elements.get('notice-text').textContent, /won’t be saved/);
  assert.equal(storageWrites.length, 0);
  assert.equal((await api.fetchProgress('circuits')).exam[1].flagged, true);
  await api.setAccount({ user: { id: 'A' } });
  assert.equal(Object.keys(api.progress).length, 0);
});

test('rapid toggles persist the final state, including unmarking', async () => {
  const { api } = viewer();
  const db = database();
  api.setClient(db);
  await api.setAccount({ user: { id: 'A' } });
  api.prepare({});
  api.saveMark(true, false);
  api.saveMark(true, true);
  api.saveMark(false, false);
  await api.drain();
  assert.deepEqual(db.writes.at(-1), {
    user_id: 'A', course_id: 'circuits', exam_id: 'exam', question_number: 1,
    done: false, flagged: false,
  });
  assert.equal(api.pending, 0);
});

test('failed saves warn, block leaving, and retry the most recent value', async () => {
  const { api, elements, listeners } = viewer();
  const db = database({ fail: true });
  api.setClient(db);
  await api.setAccount({ user: { id: 'A' } });
  api.prepare({});
  api.saveMark(true, false);
  await api.drain();
  assert.equal(api.pending, 1);
  assert.match(elements.get('notice-text').textContent, /haven’t been saved/);
  let blocked = false;
  listeners.beforeunload({ preventDefault() { blocked = true; } });
  assert.equal(blocked, true);
  db.fail = false;
  api.retryWrites();
  await api.drain();
  assert.equal(api.pending, 0);
  assert.equal(elements.get('notice').hidden, true);
});

test('switching accounts clears memory and cancels queued old-account saves', async () => {
  const { api } = viewer();
  const db = database();
  api.setClient(db);
  await api.setAccount({ user: { id: 'A' } });
  api.prepare({});
  api.saveMark(true, true);
  await api.setAccount({ user: { id: 'B' } });
  await api.drain();
  assert.equal(db.writes.length, 0);
  assert.equal(api.pending, 0);
  assert.equal(Object.keys(api.progress).length, 0);
});

test('progress loads beyond the default 1000-row limit', async () => {
  const { api } = viewer();
  const rows = Array.from({ length: 1002 }, (_, i) => ({
    exam_id: 'exam', question_number: i + 1, done: true, flagged: false,
  }));
  const db = database({ rows });
  api.setClient(db);
  await api.setAccount({ user: { id: 'A' } });
  const marks = await api.fetchProgress('circuits');
  assert.equal(Object.keys(marks.exam).length, 1002);
  assert.deepEqual(db.ranges, [[0, 999], [1000, 1999]]);
});

test('old-account fetch cannot populate a new account cache', async () => {
  const { api } = viewer();
  let complete;
  const db = database();
  const original = db.from;
  db.from = () => ({ ...original(), range() { return new Promise((resolve) => { complete = resolve; }); } });
  api.setClient(db);
  await api.setAccount({ user: { id: 'A' } });
  const fetch = api.fetchProgress('circuits');
  await api.setAccount({ user: { id: 'B' } });
  complete({ data: [{ exam_id: 'exam', question_number: 1, done: true }], error: null });
  await assert.rejects(fetch, /Account changed/);
  assert.equal(Object.keys(api.progress).length, 0);
});

test('guests see a sign-in prompt and signed-in users see the request form', async () => {
  const { api, elements } = viewer();
  await api.setAccount(null);
  assert.equal(elements.get('btn-request-courses').disabled, false);
  elements.get('btn-request-courses').onclick();
  assert.equal(elements.get('course-request-dialog').open, true);
  assert.equal(elements.get('course-request-fields').hidden, true);
  assert.equal(elements.get('course-request-sign-in').hidden, false);
  assert.equal(elements.get('course-request-description').textContent, 'Requesting another course requires sign-in.');
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.equal(elements.get('notice').hidden, true);
  api.setClient(database());
  await api.setAccount({ user: { id: 'A' } });
  assert.equal(elements.get('btn-request-courses').disabled, false);
  elements.get('btn-request-courses').onclick();
  assert.equal(elements.get('course-request-dialog').open, true);
  assert.equal(elements.get('course-request-fields').hidden, false);
  assert.equal(elements.get('course-request-sign-in').hidden, true);
  await api.setAccount(null);
  assert.equal(elements.get('course-request-dialog').open, false);
  assert.equal(elements.get('btn-request-courses').disabled, false);
});

test('course requests save the trimmed course name with the signed-in user', async () => {
  const { api, elements } = viewer();
  const requests = [];
  api.setClient({ from(table) { assert.equal(table, 'course_requests'); return {
    async insert(row) { requests.push({ ...row }); return { error: null }; },
  }; } });
  await api.setAccount({ user: { id: 'A' } });
  elements.get('btn-request-courses').onclick();
  elements.get('course-request-name').value = '  Linear Algebra  ';
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.deepEqual(requests, [{ user_id: 'A', course_name: 'Linear Algebra' }]);
  assert.equal(elements.get('course-request-dialog').open, false);
  assert.match(elements.get('notice-text').textContent, /request was sent/);
});

test('empty course requests are rejected and failed submissions retain the dialog', async () => {
  const { api, elements } = viewer();
  let calls = 0;
  api.setClient({ from() { return { async insert() { calls++; return { error: { code: '42501' } }; } }; } });
  await api.setAccount({ user: { id: 'A' } });
  elements.get('btn-request-courses').onclick();
  elements.get('course-request-name').value = '   ';
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.equal(calls, 0);
  elements.get('course-request-name').value = 'Linear Algebra';
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.equal(calls, 1);
  assert.equal(elements.get('course-request-dialog').open, true);
  assert.equal(elements.get('course-request-name').value, 'Linear Algebra');
  assert.match(elements.get('course-request-error').textContent, /Couldn’t send/);
  assert.equal(elements.get('course-request-submit').disabled, false);
});

test('double submissions are blocked and account changes ignore stale responses', async () => {
  const { api, elements } = viewer();
  let complete;
  let calls = 0;
  api.setClient({ from() { return { insert() { calls++; return new Promise((resolve) => { complete = resolve; }); } }; } });
  await api.setAccount({ user: { id: 'A' } });
  elements.get('btn-request-courses').onclick();
  elements.get('course-request-name').value = 'Linear Algebra';
  const pending = elements.get('course-request-form').onsubmit({ preventDefault() {} });
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.equal(calls, 1);
  await api.setAccount({ user: { id: 'B' } });
  complete({ error: null });
  await pending;
  assert.equal(elements.get('notice').hidden, true);
  assert.equal(elements.get('course-request-dialog').open, false);
});

test('the three-course limit shows a clear message and preserves the typed name', async () => {
  const { api, elements } = viewer();
  api.setClient({ from() { return { async insert() {
    return { error: { code: 'P0001', message: 'course_request_limit_reached' } };
  } }; } });
  await api.setAccount({ user: { id: 'A' } });
  elements.get('btn-request-courses').onclick();
  elements.get('course-request-name').value = 'Fourth course';
  await elements.get('course-request-form').onsubmit({ preventDefault() {} });
  assert.equal(elements.get('course-request-dialog').open, true);
  assert.equal(elements.get('course-request-name').value, 'Fourth course');
  assert.equal(elements.get('course-request-error').textContent,
    'You’ve reached the limit of three course requests.');
  assert.equal(elements.get('notice').hidden, true);
});
