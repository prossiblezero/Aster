import test from 'node:test';
import assert from 'node:assert/strict';
import { MAX_BACKUP_BYTES, isValidDue, validateTask, parseStore, parseBackup, mergeBackup, restoreDeletedTask, visibleTasks, summarize, encodeStore } from './logic.mjs';

const task = (id, due, extra = {}) => ({ id, title: `作业${id}`, course: '课程A', due, notes: '', completed: false, ...extra });

test('只接受真实的本地 datetime-local 日期', () => {
  assert.equal(isValidDue('2026-02-28T23:59'), true);
  assert.equal(isValidDue('2024-02-29T00:00'), true);
  assert.equal(isValidDue('2025-02-29T00:00'), false);
  assert.equal(isValidDue('2026-04-31T12:00'), false);
  assert.equal(isValidDue('2026-1-01T12:00'), false);
});

test('数据包版本、字段和重复 ID 会被完整校验', () => {
  assert.equal(parseStore('{bad').tasks.length, 0);
  assert.match(parseStore('{"version":2,"tasks":[]}').warning, /版本/);
  const duplicate = JSON.stringify({ version: 1, tasks: [task('a', '2026-01-01T10:00'), task('a', '2026-01-02T10:00')] });
  assert.match(parseStore(duplicate).warning, /ID重复/);
  assert.match(validateTask(task('x', '2026-01-01T10:00', { completed: 'no' })), /完成/);
});

test('默认截止时间升序，同一时间保持原有稳定次序', () => {
  const tasks = [task('first', '2026-03-02T10:00'), task('same-a', '2026-03-01T10:00'), task('same-b', '2026-03-01T10:00')];
  assert.deepEqual(visibleTasks(tasks).map(t => t.id), ['same-a', 'same-b', 'first']);
});

test('搜索、课程和状态筛选可以组合且搜索备注', () => {
  const tasks = [task('a', '2026-03-01T10:00', { notes: '带计算器', completed: true }), task('b', '2026-03-02T10:00', { course: '课程B', notes: '阅读' })];
  assert.deepEqual(visibleTasks(tasks, { search: '计算器', course: '课程A', status: 'done' }).map(t => t.id), ['a']);
  assert.deepEqual(visibleTasks(tasks, { status: 'todo' }).map(t => t.id), ['b']);
});

test('摘要不把已完成逾期任务计入逾期', () => {
  const tasks = [task('late', '2020-01-01T10:00'), task('done', '2020-01-01T10:00', { completed: true })];
  assert.deepEqual(summarize(tasks, new Date('2021-01-01T00:00')), { total: 2, done: 1, overdue: 1 });
});

test('存储编码符合公开 version=1 契约', () => {
  assert.deepEqual(JSON.parse(encodeStore([task('a', '2026-01-01T10:00')])), { version: 1, tasks: [task('a', '2026-01-01T10:00')] });
});

test('撤销删除恢复完整任务并保留其他任务', () => {
  const current = [task('keep', '2026-01-01T10:00')];
  const deleted = task('deleted', '2026-01-02T10:00', {
    title: '已完成的作业',
    course: '课程B',
    notes: '完整备注',
    completed: true
  });
  const restored = restoreDeletedTask(current, deleted);
  assert.deepEqual(restored, [current[0], deleted]);
  assert.deepEqual(current, [task('keep', '2026-01-01T10:00')]);
});

test('恢复已存在的 ID 时不复制任务', () => {
  const current = [task('same', '2026-01-01T10:00')];
  const restored = restoreDeletedTask(current, task('same', '2026-02-02T10:00'));
  assert.deepEqual(restored, current);
  assert.notEqual(restored, current);
});

test('备份按 ID 替换和追加，并保留未涉及的现有任务', () => {
  const current = [task('keep', '2026-01-01T10:00'), task('replace', '2026-01-02T10:00')];
  const imported = parseBackup(JSON.stringify({
    version: 1,
    tasks: [
      task('replace', '2026-02-02T10:00', { title: '导入后的任务' }),
      task('new', '2026-03-03T10:00')
    ]
  }));
  const merged = mergeBackup(current, imported);
  assert.deepEqual(merged.map(item => item.id), ['keep', 'replace', 'new']);
  assert.equal(merged[1].title, '导入后的任务');
  assert.equal(merged[0].title, current[0].title);
  assert.deepEqual(current.map(item => item.id), ['keep', 'replace']);
});

test('备份忽略额外字段并保留 HTML 片段为普通字符串', () => {
  const imported = parseBackup(JSON.stringify({
    version: 1,
    ignored: '<img src=x onerror=alert(1)>',
    tasks: [task('html', '2026-01-01T10:00', {
      title: '<script>alert(1)</script>',
      notes: '<b>普通文本</b>',
      extra: '<img src=x onerror=alert(1)>'
    })]
  }));
  assert.equal(imported[0].title, '<script>alert(1)</script>');
  assert.equal(imported[0].notes, '<b>普通文本</b>');
  assert.equal('extra' in imported[0], false);
});

test('非法备份整包拒绝且不会修改现有任务', () => {
  const current = [task('original', '2026-01-01T10:00')];
  const snapshot = structuredClone(current);
  const invalidPackages = [
    '{bad',
    JSON.stringify({ version: 2, tasks: [] }),
    JSON.stringify({ version: 1, tasks: [task('duplicate', '2026-01-01T10:00'), task('duplicate', '2026-01-02T10:00')] }),
    JSON.stringify({ version: 1, tasks: [task('bad-date', '2026-02-30T10:00')] }),
    JSON.stringify({ version: 1, tasks: [task('bad-field', '2026-01-01T10:00', { completed: 1 })] })
  ];
  for (const raw of invalidPackages) assert.throws(() => mergeBackup(current, parseBackup(raw)));
  assert.deepEqual(current, snapshot);
});

test('备份上限按 UTF-8 字节计算，超过 1 MiB 时拒绝', () => {
  const empty = JSON.stringify({ version: 1, tasks: [] });
  const exactLimit = empty + ' '.repeat(MAX_BACKUP_BYTES - new TextEncoder().encode(empty).byteLength);
  assert.equal(MAX_BACKUP_BYTES, 1024 * 1024);
  assert.deepEqual(parseBackup(exactLimit), []);
  assert.throws(() => parseBackup(`${exactLimit} `), /1 MiB/);
  assert.throws(() => parseBackup('课'.repeat(Math.ceil((MAX_BACKUP_BYTES + 1) / 3))), /1 MiB/);
});

test('空备份合法且合并结果不改变任务内容', () => {
  const current = [task('a', '2026-01-01T10:00')];
  const imported = parseBackup('{"version":1,"tasks":[]}');
  assert.deepEqual(imported, []);
  assert.deepEqual(mergeBackup(current, imported), current);
  assert.notEqual(mergeBackup(current, imported), current);
});
