const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeElement {
  constructor() {
    this.value = '';
    this._innerHTML = '';
    Object.defineProperty(this, 'innerHTML', {
      get: () => this._innerHTML,
      set: (value) => { this._innerHTML = value; if (value === '') this.children = []; }
    });
    this.textContent = '';
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.classList = { hidden: false, toggle: (name, value) => { if (name === 'hidden') this.classList.hidden = value; }, add: () => { this.classList.hidden = true; }, remove: () => { this.classList.hidden = false; } };
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  append(...children) { this.children.push(...children); }
  setAttribute() {}
  focus() {}
  reset() {}
  closest() { return this; }
  click() { this.listeners.click?.({ target: this }); }
}

function createApp(seed) {
  const ids = ['start-date', 'daily-limit', 'course-form', 'course-name', 'exam-date', 'remaining-hours', 'course-submit', 'cancel-edit', 'course-list', 'course-empty', 'course-count', 'status-area', 'generate-plan', 'plan-list', 'plan-empty', 'plan-meta', 'summary-strip', 'planned-hours', 'planned-days', 'gap-hours', 'saved-note'];
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement()]));
  const storage = { value: JSON.stringify(seed), getItem() { return this.value; }, setItem(_, value) { this.value = value; } };
  const document = { getElementById: (id) => elements[id], createElement: () => new FakeElement() };
  const context = { document, localStorage: storage, window: { confirm: () => true }, console, Date, Math, Number, JSON, Set };
  vm.runInNewContext(fs.readFileSync('app.js', 'utf8'), context);
  return { elements, storage };
}

test('project files and required UI contract exist', () => {
  for (const file of ['index.html', 'styles.css', 'app.js', 'README.md']) assert.equal(fs.existsSync(file), true, file);
  const html = fs.readFileSync('index.html', 'utf8');
  for (const marker of ['开始日期', '每日上限（小时）', '课程名称', '考试日期', '剩余小时', '添加课程', '生成计划', '课程列表', '复习计划', 'role="status"']) assert.ok(html.includes(marker), marker);
});

test('real app path clears a deleted course plan before saving', () => {
  const app = createApp({
    startDate: '2025-06-01', dailyLimit: 2,
    courses: [{ id: 'course-1', name: '数学', examDate: '2025-06-04', hours: 2 }],
    plan: [{ date: '2025-06-01', course: '数学', hours: 2 }]
  });
  const courseRow = app.elements['course-list'].children[0];
  const deleteButton = courseRow.children[3].children[1];
  app.elements['course-list'].listeners.click({ target: deleteButton });
  assert.equal(JSON.parse(app.storage.value).courses.length, 0);

  app.elements['generate-plan'].click();
  const saved = JSON.parse(app.storage.value);
  assert.deepEqual(saved.plan, []);
  assert.equal(app.elements['plan-list'].children.length, 0);
  assert.equal(app.elements['summary-strip'].classList.hidden, true);
  assert.equal(app.elements['planned-hours'].textContent, '0');
  assert.equal(app.elements['planned-days'].textContent, '0');
  assert.equal(app.elements['gap-hours'].textContent, '0');

  const refreshed = createApp(saved);
  assert.equal(refreshed.elements['plan-list'].children.length, 0);
  assert.equal(refreshed.elements['summary-strip'].classList.hidden, true);
});
