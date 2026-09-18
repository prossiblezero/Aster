(() => {
  'use strict';

  const STORAGE_KEY = 'final-review-planner-v1';
  const $ = (id) => document.getElementById(id);
  const els = {
    startDate: $('start-date'), dailyLimit: $('daily-limit'), form: $('course-form'),
    courseName: $('course-name'), examDate: $('exam-date'), remainingHours: $('remaining-hours'),
    submit: $('course-submit'), cancelEdit: $('cancel-edit'), courseList: $('course-list'),
    courseEmpty: $('course-empty'), courseCount: $('course-count'), status: $('status-area'),
    generate: $('generate-plan'), planList: $('plan-list'), planEmpty: $('plan-empty'),
    planMeta: $('plan-meta'), summary: $('summary-strip'), plannedHours: $('planned-hours'),
    plannedDays: $('planned-days'), gapHours: $('gap-hours'), savedNote: $('saved-note')
  };
  let state = { startDate: '', dailyLimit: '', courses: [], plan: [] };
  let editingId = null;

  function todayString() {
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000;
    return new Date(now.getTime() - offset).toISOString().slice(0, 10);
  }

  function parseDate(value) {
    const parts = value.split('-').map(Number);
    return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  }

  function dateString(date) { return date.toISOString().slice(0, 10); }
  function addDays(value, days) {
    const date = parseDate(value);
    date.setUTCDate(date.getUTCDate() + days);
    return dateString(date);
  }
  function roundHours(value) { return Math.round((value + Number.EPSILON) * 100) / 100; }
  function formatHours(value) { return Number.isInteger(value) ? String(value) : String(roundHours(value)); }
  function validDate(value) {
    return /^\d{4}-\d{2}-\d{2}$/.test(value)
      && !Number.isNaN(parseDate(value).getTime())
      && dateString(parseDate(value)) === value;
  }
  function validHours(value) { return Number.isFinite(Number(value)) && Number(value) > 0; }
  function makeId() { return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }

  function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    els.savedNote.textContent = '已自动保存';
  }

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && typeof saved === 'object') state = {
        startDate: typeof saved.startDate === 'string' ? saved.startDate : '',
        dailyLimit: saved.dailyLimit ?? '',
        courses: Array.isArray(saved.courses) ? saved.courses : [],
        plan: Array.isArray(saved.plan) ? saved.plan : []
      };
    } catch (_) { /* Ignore malformed browser data and start clean. */ }
    if (!state.startDate) state.startDate = todayString();
    els.startDate.value = state.startDate;
    els.dailyLimit.value = state.dailyLimit;
    renderCourses();
    renderPlan(state.plan);
  }

  function setStatus(title, detail, type = 'success') {
    els.status.innerHTML = '';
    if (!title) return;
    const box = document.createElement('div');
    box.className = `status-message ${type}`;
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = type === 'error' ? '!' : type === 'warning' ? '△' : '✓';
    const copy = document.createElement('div');
    const heading = document.createElement('strong');
    heading.textContent = title;
    copy.append(heading);
    if (detail) { const text = document.createElement('span'); text.textContent = detail; copy.append(text); }
    box.append(icon, copy);
    els.status.append(box);
  }

  function renderCourses() {
    els.courseList.innerHTML = '';
    els.courseCount.textContent = `${state.courses.length} 门课程`;
    els.courseEmpty.classList.toggle('hidden', state.courses.length > 0);
    state.courses.forEach((course) => {
      const row = document.createElement('tr');
      const name = document.createElement('td'); name.className = 'course-name-cell'; name.textContent = course.name;
      const exam = document.createElement('td'); exam.textContent = course.examDate;
      const hours = document.createElement('td'); hours.textContent = `${formatHours(Number(course.hours))} 小时`;
      const actions = document.createElement('td'); actions.className = 'actions';
      const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'button button-quiet button-small'; edit.textContent = '编辑'; edit.dataset.action = 'edit'; edit.dataset.id = course.id;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-quiet button-small'; remove.textContent = '删除'; remove.dataset.action = 'delete'; remove.dataset.id = course.id;
      actions.append(edit, remove); row.append(name, exam, hours, actions); els.courseList.append(row);
    });
  }

  function renderPlan(plan) {
    els.planList.innerHTML = '';
    els.planEmpty.classList.toggle('hidden', plan.length > 0);
    const planned = roundHours(plan.reduce((sum, item) => sum + Number(item.hours), 0));
    const days = new Set(plan.map((item) => item.date)).size;
    els.summary.classList.toggle('hidden', plan.length === 0 && !state.plan.length);
    els.plannedHours.textContent = formatHours(planned);
    els.plannedDays.textContent = String(days);
    els.gapHours.textContent = '0';
    plan.forEach((item) => {
      const row = document.createElement('tr');
      const date = document.createElement('td'); date.textContent = item.date;
      const course = document.createElement('td'); course.textContent = item.course;
      const hours = document.createElement('td'); hours.textContent = `${formatHours(Number(item.hours))} 小时`;
      row.append(date, course, hours); els.planList.append(row);
    });
  }

  function persistSettings() {
    state.startDate = els.startDate.value;
    state.dailyLimit = els.dailyLimit.value;
    saveState();
  }

  function validateCourse() {
    const name = els.courseName.value.trim();
    const examDate = els.examDate.value;
    const hours = Number(els.remainingHours.value);
    if (!name) return '请填写课程名称。';
    if (!validDate(examDate)) return '请填写有效的考试日期。';
    if (!validHours(hours)) return '剩余小时必须是大于 0 的有限数字。';
    return null;
  }

  function clearCourseForm() {
    els.form.reset(); editingId = null; els.submit.innerHTML = '<span aria-hidden="true">＋</span> 添加课程'; els.cancelEdit.classList.add('hidden');
  }

  function addOrSaveCourse(event) {
    event.preventDefault();
    const error = validateCourse();
    if (error) { setStatus('课程未保存', error, 'error'); return; }
    const course = { name: els.courseName.value.trim(), examDate: els.examDate.value, hours: roundHours(Number(els.remainingHours.value)) };
    if (editingId) {
      const index = state.courses.findIndex((item) => item.id === editingId);
      if (index >= 0) state.courses[index] = { ...state.courses[index], ...course };
      setStatus('课程已修改', '如需更新安排，请重新生成计划。');
    } else {
      state.courses.push({ id: makeId(), ...course });
      setStatus('课程已添加', '可以继续添加课程，或直接生成计划。');
    }
    clearCourseForm(); saveState(); renderCourses();
  }

  function editCourse(id) {
    const course = state.courses.find((item) => item.id === id);
    if (!course) return;
    editingId = id; els.courseName.value = course.name; els.examDate.value = course.examDate; els.remainingHours.value = course.hours;
    els.submit.innerHTML = '<span aria-hidden="true">✓</span> 保存修改'; els.cancelEdit.classList.remove('hidden'); els.courseName.focus();
  }

  function deleteCourse(id) {
    const course = state.courses.find((item) => item.id === id);
    if (!course || !window.confirm(`确定删除“${course.name}”吗？`)) return;
    state.courses = state.courses.filter((item) => item.id !== id); saveState(); renderCourses();
    setStatus('课程已删除', '已有计划不会自动改变；如需同步，请重新生成计划。');
  }

  function generatePlan() {
    const start = els.startDate.value;
    const limit = Number(els.dailyLimit.value);
    if (!validDate(start)) { setStatus('无法生成计划', '请填写有效的开始日期。', 'error'); return; }
    if (!validHours(limit)) { setStatus('无法生成计划', '每日上限必须是大于 0 的有限数字。', 'error'); return; }
    if (!state.courses.length) {
      state.plan = [];
      saveState();
      renderPlan([]);
      els.planMeta.textContent = '设置课程和时间后生成你的计划。';
      setStatus('无法生成计划', '请先添加至少一门课程。', 'error');
      return;
    }
    state.startDate = start; state.dailyLimit = roundHours(limit);
    const courses = state.courses.map((course) => ({ ...course, remaining: Number(course.hours) })).sort((a, b) => a.examDate.localeCompare(b.examDate) || a.name.localeCompare(b.name));
    const latestExam = courses.reduce((latest, course) => course.examDate > latest ? course.examDate : latest, start);
    const plan = [];
    let date = start;
    while (date < latestExam && courses.some((course) => course.remaining > 0)) {
      let available = limit;
      courses.forEach((course) => {
        if (available <= 0 || course.remaining <= 0 || date >= course.examDate) return;
        const amount = roundHours(Math.min(available, course.remaining));
        if (amount > 0) { plan.push({ date, course: course.name, hours: amount }); course.remaining = roundHours(course.remaining - amount); available = roundHours(available - amount); }
      });
      date = addDays(date, 1);
    }
    state.plan = plan; saveState(); renderPlan(plan);
    const totalNeeded = roundHours(state.courses.reduce((sum, course) => sum + Number(course.hours), 0));
    const totalPlanned = roundHours(plan.reduce((sum, item) => sum + Number(item.hours), 0));
    const gap = roundHours(totalNeeded - totalPlanned);
    els.gapHours.textContent = formatHours(gap);
    els.summary.classList.remove('hidden');
    els.planMeta.textContent = plan.length ? `${plan[0].date} 至 ${plan[plan.length - 1].date} · 共 ${formatHours(totalPlanned)} 小时` : '没有可安排的复习日。';
    if (gap > 0) setStatus('计划已生成，但存在不足', `已安排 ${formatHours(totalPlanned)} 小时，仍缺少 ${formatHours(gap)} 小时。考试日当天不安排复习，请调整开始日期、每日上限或课程时数。`, 'warning');
    else setStatus('计划已生成', `全部 ${formatHours(totalPlanned)} 小时已安排完成，考试日当天不安排复习。`);
  }

  els.form.addEventListener('submit', addOrSaveCourse);
  els.cancelEdit.addEventListener('click', () => { clearCourseForm(); setStatus('', ''); });
  els.courseList.addEventListener('click', (event) => { const button = event.target.closest('button'); if (!button) return; button.dataset.action === 'edit' ? editCourse(button.dataset.id) : deleteCourse(button.dataset.id); });
  els.generate.addEventListener('click', generatePlan);
  els.startDate.addEventListener('change', persistSettings);
  els.dailyLimit.addEventListener('change', persistSettings);
  loadState();
})();
