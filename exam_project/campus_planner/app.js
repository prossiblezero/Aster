import { STORAGE_KEY, MAX_BACKUP_BYTES, parseStore, encodeStore, parseBackup, mergeBackup, restoreDeletedTask, makeTask, makeId, validateTask, visibleTasks, summarize, isOverdue } from './logic.mjs';
import { buildCalendar } from './calendar.mjs';

const $ = id => document.getElementById(id);
let tasks = [];
let warning = '';
let needsRecoveryConfirmation = false;
let undoTask = null;
try {
  const loaded = parseStore(localStorage.getItem(STORAGE_KEY));
  tasks = loaded.tasks;
  warning = loaded.warning;
  needsRecoveryConfirmation = Boolean(warning);
} catch {
  warning = '本地存储不可用：当前修改将未保存，刷新或关闭页面会丢失。';
}

function notify(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('is-error', error);
}

function commit(nextTasks, action) {
  if (needsRecoveryConfirmation && !window.confirm('原本地数据无法读取。继续将用当前清单替换原数据，是否继续？')) return false;
  tasks = nextTasks;
  try {
    localStorage.setItem(STORAGE_KEY, encodeStore(tasks));
    needsRecoveryConfirmation = false;
    $('storage-status').textContent = '';
    notify(`${action}，已保存到此浏览器。`);
  } catch {
    $('storage-status').textContent = '未保存：本地存储不可用或空间不足。当前清单仅保留在页面内，刷新或关闭会丢失本次修改。';
    notify(`${action}，但未保存。`, true);
  }
  render();
  return true;
}

function importTasks(importedTasks) {
  if (!importedTasks.length) {
    notify('导入完成：备份中没有任务，清单未更改。');
    return;
  }
  if (needsRecoveryConfirmation && !window.confirm('原本地数据无法读取。继续将用合并后的清单替换原数据，是否继续？')) {
    notify('导入已取消，清单未更改。', true);
    return;
  }

  const nextTasks = mergeBackup(tasks, importedTasks);
  try {
    localStorage.setItem(STORAGE_KEY, encodeStore(nextTasks));
  } catch {
    notify('导入失败：无法写入本地存储，原清单未更改。', true);
    return;
  }
  tasks = nextTasks;
  needsRecoveryConfirmation = false;
  $('storage-status').textContent = '';
  render();
  notify(`导入完成：已合并 ${importedTasks.length} 条任务。`);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function render() {
  const undoButton = $('undo-delete');
  undoButton.classList.toggle('hidden', !undoTask);
  undoButton.disabled = !undoTask;
  const counts = summarize(tasks);
  $('total-count').textContent = counts.total - counts.done;
  $('done-count').textContent = counts.done;
  $('overdue-count').textContent = counts.overdue;
  const selectedCourse = $('course-filter').value;
  const courses = [...new Set(tasks.map(task => task.course))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  $('course-filter').replaceChildren(new Option('全部课程', ''), ...courses.map(course => new Option(course, course)));
  $('course-filter').value = courses.includes(selectedCourse) ? selectedCourse : '';
  const shown = visibleTasks(tasks, { search: $('search').value, course: $('course-filter').value, status: $('status-filter').value });
  const list = $('task-list');
  list.replaceChildren();
  if (!shown.length) {
    const empty = element('div', 'empty');
    empty.append(element('span', 'empty-symbol', '☑'), element('h2', '', tasks.length ? '没有匹配的作业' : '清单还是空的'), element('p', '', tasks.length ? '换个关键词或筛选条件再看看。' : '添加第一份作业，开始安排你的课业。'));
    list.append(empty);
    return;
  }
  for (const task of shown) {
    const card = element('article', `task-card${task.completed ? ' is-done' : ''}`);
    card.dataset.testid = 'task-card';
    card.dataset.taskId = task.id;
    const content = element('div', 'task-content');
    const meta = element('div', 'task-meta');
    meta.append(element('span', 'course-tag', task.course));
    meta.append(element('span', `overdue-tag${isOverdue(task) ? '' : ' hidden'}`, '逾期'));
    if (task.completed) meta.append(element('span', 'done-tag', '已完成'));
    const heading = element('h2', 'task-title', task.title);
    const time = element('time', 'due', `截止 ${task.due.replace('T', ' ')}`);
    time.dateTime = task.due;
    content.append(meta, heading, time);
    if (task.notes) content.append(element('p', 'notes', task.notes));
    const actions = element('div', 'task-actions');
    for (const [label, action] of [[task.completed ? '恢复' : '完成', 'toggle'], ['编辑', 'edit'], ['删除', 'delete']]) {
      const button = element('button', action === 'delete' ? 'danger-button' : 'secondary', label);
      button.type = 'button';
      button.dataset.action = action;
      button.addEventListener('click', () => handleAction(task.id, action));
      actions.append(button);
    }
    card.append(content, actions);
    list.append(card);
  }
}

function resetForm() {
  $('task-form').reset();
  $('editing-id').value = '';
  $('form-title').textContent = '添加作业';
  $('submit-btn').textContent = '添加作业';
  $('cancel-edit').classList.add('hidden');
  $('form-error').textContent = '';
  for (const id of ['title', 'course', 'due']) $(id).removeAttribute('aria-invalid');
}

function focusCard(id, action) {
  const cards = [...$('task-list').children].filter(card => card.dataset.taskId);
  const card = cards.find(item => item.dataset.taskId === id) || cards[0];
  const button = card && [...card.querySelectorAll('button')].find(item => item.dataset.action === action);
  (button || $('title')).focus();
}

function handleAction(id, action) {
  const task = tasks.find(t => t.id === id);
  if (!task) return;
  if (action === 'edit') {
    $('editing-id').value = id;
    for (const field of ['title', 'course', 'due', 'notes']) $(field).value = task[field];
    $('form-title').textContent = '编辑作业';
    $('submit-btn').textContent = '保存修改';
    $('cancel-edit').classList.remove('hidden');
    $('form-error').textContent = '';
    $('title').focus();
    $('task-form').scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  if (action === 'delete') {
    if (!window.confirm(`确定删除“${task.title}”？确认后可使用“撤销删除”恢复最近删除的一条。`)) return;
    if (commit(tasks.filter(t => t.id !== id), '作业已删除')) {
      undoTask = { ...task };
      render();
      if ($('editing-id').value === id) resetForm();
      $('undo-delete').focus();
    }
  } else {
    if (commit(tasks.map(t => t.id === id ? { ...t, completed: !t.completed } : t), task.completed ? '作业已恢复' : '作业已完成')) focusCard(id, action);
  }
}

$('task-form').addEventListener('submit', event => {
  event.preventDefault();
  for (const id of ['title', 'course', 'due']) $(id).removeAttribute('aria-invalid');
  const editingId = $('editing-id').value;
  let id = editingId || makeId();
  while (!editingId && tasks.some(t => t.id === id)) id = makeId();
  const task = makeTask({ title: $('title').value, course: $('course').value, due: $('due').value, notes: $('notes').value }, id);
  const original = tasks.find(t => t.id === editingId);
  if (original) task.completed = original.completed;
  const error = validateTask(task);
  if (error) {
    $('form-error').textContent = error;
    const field = !task.title ? 'title' : !task.course ? 'course' : 'due';
    $(field).setAttribute('aria-invalid', 'true');
    $(field).focus();
    return;
  }
  const next = original ? tasks.map(t => t.id === editingId ? task : t) : [...tasks, task];
  if (commit(next, original ? '作业已修改' : '作业已添加')) {
    resetForm();
    $('title').focus();
  }
});
$('cancel-edit').addEventListener('click', () => { resetForm(); $('title').focus(); });
$('undo-delete').addEventListener('click', () => {
  if (!undoTask) return;
  const deletedTask = undoTask;
  if (tasks.some(task => task.id === deletedTask.id)) {
    undoTask = null;
    render();
    notify('撤销失败：该任务已存在，未复制任务。', true);
    return;
  }
  if (commit(restoreDeletedTask(tasks, deletedTask), '删除已撤销，作业已恢复')) {
    undoTask = null;
    render();
    $('undo-delete').focus();
  }
});
for (const id of ['search', 'course-filter', 'status-filter']) $(id).addEventListener(id === 'search' ? 'input' : 'change', render);
function download(content, type, filename) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

$('export-calendar').addEventListener('click', () => {
  download(buildCalendar(tasks), 'text/calendar;charset=utf-8', 'campus-planner.ics');
  notify('日历已导出，仅包含未完成作业，清单未更改。');
});
$('export-backup').addEventListener('click', () => {
  download(encodeStore(tasks), 'application/json;charset=utf-8', 'campus-planner-backup.json');
  notify('备份已导出，清单未更改。');
});
$('backup-input').addEventListener('change', async event => {
  const input = event.currentTarget;
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    if (file.size > MAX_BACKUP_BYTES) throw new Error('备份文件超过 1 MiB 上限。');
    importTasks(parseBackup(await file.text()));
  } catch (error) {
    notify(`导入失败：${error instanceof Error ? error.message : '无法读取备份文件。'} 原清单未更改。`, true);
  } finally {
    input.value = '';
  }
});
$('sample-btn').addEventListener('click', () => {
  if (tasks.length) {
    notify('清单已有作业，未加载示例，也未覆盖现有数据。', true);
    return;
  }
  const localDue = (offset, hour) => {
    const date = new Date();
    date.setDate(date.getDate() + offset);
    return `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${hour}:00`;
  };
  const examples = [
    { title: '极限与连续习题', course: '高等数学（虚构）', due: localDue(1, '20'), notes: '完成第 2 章练习题。' },
    { title: '校园观察短文', course: '大学写作（虚构）', due: localDue(3, '18'), notes: '整理观察笔记与文章提纲。' },
    { title: '数组练习', course: '程序设计（虚构）', due: localDue(-1, '22'), notes: '检查边界条件，补充测试用例。' }
  ].map(item => makeTask(item));
  commit(examples, '示例已加载');
});
if (warning) $('storage-status').textContent = warning;
render();
// 仅更新时间标识，避免定时重绘打断键盘焦点。
setInterval(() => {
  $('overdue-count').textContent = summarize(tasks).overdue;
  for (const card of $('task-list').children) {
    const task = tasks.find(item => item.id === card.dataset.taskId);
    if (task) card.querySelector('.overdue-tag').classList.toggle('hidden', !isOverdue(task));
  }
}, 60000);
