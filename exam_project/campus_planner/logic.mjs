// 无 DOM 依赖的课业清单规则，浏览器和 Node 测试均可使用。
export const STORAGE_KEY = 'campus-planner-v1';
export const MAX_BACKUP_BYTES = 1024 * 1024;

export function isValidDue(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return false;
  const [date, time] = value.split('T');
  const [year, month, day] = date.split('-').map(Number);
  const [hour, minute] = time.split(':').map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return day <= days;
}

export function validateTask(task) {
  if (!task || typeof task !== 'object') return '记录不是对象';
  if (typeof task.id !== 'string' || !task.id.trim()) return '缺少有效ID';
  if (typeof task.title !== 'string' || !task.title.trim()) return '作业名称不能为空';
  if (typeof task.course !== 'string' || !task.course.trim()) return '课程不能为空';
  if (!isValidDue(task.due)) return '截止时间格式或日期无效';
  if (typeof task.notes !== 'string') return '备注必须是文本';
  if (typeof task.completed !== 'boolean') return '完成状态无效';
  return null;
}

export function parseStore(raw) {
  if (raw === null) return { tasks: [], warning: '' };
  let data;
  try { data = JSON.parse(raw); } catch { return { tasks: [], warning: '本地数据无法读取（JSON 已损坏），已使用空清单。' }; }
  if (!data || data.version !== 1 || !Array.isArray(data.tasks)) {
    return { tasks: [], warning: '本地数据版本或结构不受支持，已使用空清单。' };
  }
  const seen = new Set();
  for (const task of data.tasks) {
    const error = validateTask(task);
    if (error || seen.has(task.id)) return { tasks: [], warning: `本地数据无效（${error || 'ID重复'}），已使用空清单。` };
    seen.add(task.id);
  }
  return { tasks: data.tasks.map(task => ({ ...task })), warning: '' };
}

export function makeTask({ title, course, due, notes = '' }, id = makeId()) {
  return { id, title: title.trim(), course: course.trim(), due, notes: String(notes), completed: false };
}

export function makeId() {
  return `task-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function isOverdue(task, now = new Date()) {
  return !task.completed && new Date(task.due) < now;
}

export function visibleTasks(tasks, { search = '', course = '', status = 'all' } = {}) {
  const query = search.trim().toLocaleLowerCase();
  return tasks
    .map((task, index) => ({ task, index }))
    .filter(({ task }) => !query || [task.title, task.course, task.notes].some(v => v.toLocaleLowerCase().includes(query)))
    .filter(({ task }) => !course || task.course === course)
    .filter(({ task }) => status === 'all' || (status === 'done' ? task.completed : !task.completed))
    .sort((a, b) => a.task.due.localeCompare(b.task.due) || a.index - b.index)
    .map(({ task }) => task);
}

export function summarize(tasks, now = new Date()) {
  return {
    total: tasks.length,
    done: tasks.filter(t => t.completed).length,
    overdue: tasks.filter(t => isOverdue(t, now)).length
  };
}

export function encodeStore(tasks) { return JSON.stringify({ version: 1, tasks }); }

function backupError(message) {
  throw new Error(message);
}

export function parseBackup(raw) {
  if (typeof raw !== 'string') backupError('无法读取备份文件。');
  if (new TextEncoder().encode(raw).byteLength > MAX_BACKUP_BYTES) backupError('备份文件超过 1 MiB 上限。');

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    backupError('备份不是合法的 JSON。');
  }
  if (!data || typeof data !== 'object' || data.version !== 1 || !Array.isArray(data.tasks)) {
    backupError('备份版本或结构不受支持。');
  }

  const seen = new Set();
  return data.tasks.map((task, index) => {
    const error = validateTask(task);
    if (error) backupError(`第 ${index + 1} 条任务无效：${error}。`);
    if (seen.has(task.id)) backupError(`备份中存在重复 ID：${task.id}。`);
    seen.add(task.id);
    return {
      id: task.id,
      title: task.title,
      course: task.course,
      due: task.due,
      notes: task.notes,
      completed: task.completed
    };
  });
}

export function mergeBackup(currentTasks, importedTasks) {
  const positions = new Map(currentTasks.map((task, index) => [task.id, index]));
  const merged = currentTasks.map(task => ({ ...task }));
  for (const task of importedTasks) {
    const position = positions.get(task.id);
    if (position === undefined) {
      positions.set(task.id, merged.length);
      merged.push({ ...task });
    } else {
      merged[position] = { ...task };
    }
  }
  return merged;
}

export function restoreDeletedTask(currentTasks, deletedTask) {
  if (!deletedTask || currentTasks.some(task => task.id === deletedTask.id)) {
    return currentTasks.map(task => ({ ...task }));
  }
  return [...currentTasks.map(task => ({ ...task })), { ...deletedTask }];
}
