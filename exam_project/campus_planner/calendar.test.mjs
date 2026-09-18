import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCalendar, escapeCalendarText, foldCalendarLine, shanghaiDueToUtc } from './calendar.mjs';

const task = (id, due, extra = {}) => ({
  id,
  title: '期末报告',
  course: '社会学',
  due,
  notes: '',
  completed: false,
  ...extra
});

function unfold(calendar) {
  return calendar.replace(/\r\n /g, '');
}

test('空清单仍导出以 CRLF 结尾的合法 VCALENDAR', () => {
  const calendar = buildCalendar([], new Date('2026-01-02T03:04:05Z'));
  assert.equal(calendar, [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Campus Planner//Coursework Calendar//ZH-CN',
    'END:VCALENDAR',
    ''
  ].join('\r\n'));
  assert.equal(/(^|[^\r])\n/.test(calendar), false);
});

test('只导出未完成任务且 UID 由任务 ID 稳定生成', () => {
  const tasks = [
    task('todo-1', '2026-10-08T23:59'),
    task('done-1', '2026-10-09T10:00', { completed: true })
  ];
  const first = unfold(buildCalendar(tasks, new Date('2026-01-02T03:04:05Z')));
  const later = unfold(buildCalendar(tasks, new Date('2026-02-03T04:05:06Z')));

  assert.match(first, /UID:todo-1@campus-planner\.local\r\n/);
  assert.doesNotMatch(first, /done-1/);
  assert.equal((first.match(/BEGIN:VEVENT/g) || []).length, 1);
  assert.match(first, /DTSTAMP:20260102T030405Z/);
  assert.match(later, /UID:todo-1@campus-planner\.local\r\n/);
  assert.match(later, /DTSTAMP:20260203T040506Z/);
});

test('北京时间固定转为 UTC，结束时间加 30 分钟并允许跨日', () => {
  assert.equal(shanghaiDueToUtc('2026-10-08T23:59').toISOString(), '2026-10-08T15:59:00.000Z');
  const calendar = unfold(buildCalendar([task('cross-day', '2026-10-09T00:15')], new Date('2026-01-01T00:00:00Z')));
  assert.match(calendar, /DTSTART:20261008T161500Z\r\nDTEND:20261008T164500Z/);

  const utcCrossDay = unfold(buildCalendar([task('end-cross-day', '2026-10-09T07:45')], new Date('2026-01-01T00:00:00Z')));
  assert.match(utcCrossDay, /DTSTART:20261008T234500Z\r\nDTEND:20261009T001500Z/);
});

test('SUMMARY 和 DESCRIPTION 按规定顺序转义文本', () => {
  assert.equal(escapeCalendarText('反\\斜线\r\n换行,逗号;分号'), '反\\\\斜线\\n换行\\,逗号\\;分号');
  const calendar = unfold(buildCalendar([
    task('escaped', '2026-10-08T12:00', {
      course: '课,程',
      title: '题;目',
      notes: '第一行\\内容\n第二行,末尾;'
    })
  ], new Date('2026-01-01T00:00:00Z')));
  assert.match(calendar, /SUMMARY:课\\,程：题\\;目\r\n/);
  assert.match(calendar, /DESCRIPTION:第一行\\\\内容\\n第二行\\,末尾\\;\r\n/);
});

test('长行按 UTF-8 字节折行，续行计入空格且不切断字符', () => {
  const logicalLine = `DESCRIPTION:${'中文🙂abc'.repeat(20)}`;
  const folded = foldCalendarLine(logicalLine);
  const physicalLines = folded.split('\r\n');

  assert.ok(physicalLines.length > 1);
  assert.ok(physicalLines.slice(1).every(line => line.startsWith(' ')));
  assert.ok(physicalLines.every(line => new TextEncoder().encode(line).byteLength <= 75));
  assert.equal(folded.replace(/\r\n /g, ''), logicalLine);
  assert.equal(folded.includes('\uFFFD'), false);
});

test('完整日历的每个物理行不超过 75 个 UTF-8 字节', () => {
  const calendar = buildCalendar([
    task('long', '2026-10-08T12:00', {
      course: '计算机科学导论'.repeat(8),
      title: '多语言🙂课程报告'.repeat(8),
      notes: '备注，包含中文；以及 emoji 🙂。'.repeat(12)
    })
  ], new Date('2026-01-01T00:00:00Z'));

  const lines = calendar.split('\r\n');
  assert.equal(lines.at(-1), '');
  assert.ok(lines.slice(0, -1).every(line => new TextEncoder().encode(line).byteLength <= 75));
});
