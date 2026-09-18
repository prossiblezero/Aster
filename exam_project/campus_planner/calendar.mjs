const encoder = new TextEncoder();
const CALENDAR_PRODID = '-//Campus Planner//Coursework Calendar//ZH-CN';

export function escapeCalendarText(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/\r\n|\r|\n/g, '\\n')
    .replace(/,/g, '\\,')
    .replace(/;/g, '\\;');
}

export function foldCalendarLine(line) {
  const physicalLines = [];
  let current = '';

  for (const character of line) {
    if (encoder.encode(current + character).byteLength > 75) {
      physicalLines.push(current);
      current = ` ${character}`;
    } else {
      current += character;
    }
  }
  physicalLines.push(current);
  return physicalLines.join('\r\n');
}

function formatUtc(date) {
  const year = String(date.getUTCFullYear()).padStart(4, '0');
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  const hour = String(date.getUTCHours()).padStart(2, '0');
  const minute = String(date.getUTCMinutes()).padStart(2, '0');
  const second = String(date.getUTCSeconds()).padStart(2, '0');
  return `${year}${month}${day}T${hour}${minute}${second}Z`;
}

export function shanghaiDueToUtc(due) {
  const [year, month, day, hour, minute] = due.match(/\d+/g).map(Number);
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour - 8, minute, 0, 0);
  return date;
}

export function buildCalendar(tasks, exportedAt = new Date()) {
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    `PRODID:${CALENDAR_PRODID}`
  ];
  const stamp = formatUtc(exportedAt);

  for (const task of tasks.filter(item => !item.completed)) {
    const start = shanghaiDueToUtc(task.due);
    const end = new Date(start.getTime() + 30 * 60 * 1000);
    lines.push(
      'BEGIN:VEVENT',
      `UID:${escapeCalendarText(task.id)}@campus-planner.local`,
      `DTSTAMP:${stamp}`,
      `DTSTART:${formatUtc(start)}`,
      `DTEND:${formatUtc(end)}`,
      `SUMMARY:${escapeCalendarText(`${task.course}：${task.title}`)}`,
      `DESCRIPTION:${escapeCalendarText(task.notes)}`,
      'END:VEVENT'
    );
  }

  lines.push('END:VCALENDAR');
  return `${lines.map(foldCalendarLine).join('\r\n')}\r\n`;
}
