import test from 'node:test';
import assert from 'node:assert/strict';
import { isValidDue, validateTask, parseStore, visibleTasks, summarize, encodeStore } from './logic.mjs';

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
