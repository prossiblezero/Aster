(() => {
  'use strict';

  const root = document.documentElement;
  const body = document.body;
  const lesson = body.dataset.lesson || '';
  const siteRoot = new URL(`${body.dataset.root || '.'}/`, window.location.href);
  const slugs = [
    'project-story',
    '00-quickstart', '01-agent-loop', '02-model-protocols', '03-tool-system',
    '04-permissions-sandbox', '05-state-and-evidence', '06-context-budget',
    '07-context-archives', '08-hooks-and-completion', '09-checkpoint-resume',
    '10-layered-memory', '11-memory-evolution', '12-plans-and-skills',
    '13-interaction-and-workers', '14-evaluation', '15-rl-interface'
  ];
  const validLessons = new Set(slugs);
  const memoryStorage = new Map();
  const storage = {
    get(key) {
      try {
        const value = window.localStorage.getItem(key);
        return value === null ? (memoryStorage.get(key) ?? null) : value;
      } catch (_) {
        return memoryStorage.get(key) ?? null;
      }
    },
    set(key, value) {
      memoryStorage.set(key, value);
      try { window.localStorage.setItem(key, value); } catch (_) {}
    }
  };

  const savedTheme = storage.get('aster-theme');
  if (savedTheme === 'dark' || savedTheme === 'light') root.dataset.theme = savedTheme;

  let done = readDone();
  const toggle = document.querySelector('.completed-toggle');
  const progress = document.querySelector('.progress-text');

  function readDone() {
    const value = storage.get('aster-done');
    if (!value) return {};
    try {
      const parsed = JSON.parse(value);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
      return Object.fromEntries(
        slugs.filter(slug => parsed[slug] === true).map(slug => [slug, true])
      );
    } catch (_) {
      return {};
    }
  }

  function updateProgress() {
    if (progress) progress.textContent = `${Object.keys(done).length} / ${slugs.length} 篇已完成`;
  }

  if (toggle && validLessons.has(lesson)) {
    toggle.checked = done[lesson] === true;
    toggle.addEventListener('change', () => {
      if (toggle.checked) done[lesson] = true;
      else delete done[lesson];
      storage.set('aster-done', JSON.stringify(done));
      updateProgress();
    });
  }
  updateProgress();

  const themeButton = document.getElementById('theme-toggle');
  themeButton?.addEventListener('click', () => {
    const value = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = value;
    storage.set('aster-theme', value);
  });

  const menu = document.getElementById('menu-toggle');
  const side = document.querySelector('.sidebar');
  const overlay = document.querySelector('.mobile-overlay');
  function menuState(open) {
    side?.classList.toggle('open', open);
    overlay?.classList.toggle('show', open);
    menu?.setAttribute('aria-expanded', String(open));
  }
  if (menu) {
    menu.setAttribute('aria-expanded', side?.classList.contains('open') ? 'true' : 'false');
    menu.addEventListener('click', () => menuState(!side?.classList.contains('open')));
  }
  overlay?.addEventListener('click', () => menuState(false));
  side?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => menuState(false)));

  if (validLessons.has(lesson)) storage.set('aster-last', lesson);
  const continueLink = document.getElementById('continue-link');
  const last = storage.get('aster-last');
  if (continueLink && validLessons.has(last)) {
    continueLink.href = new URL(`lessons/${last}.html`, siteRoot).href;
    continueLink.textContent = '继续上次阅读 →';
  }

  document.querySelectorAll('.copy-code').forEach(button => {
    button.addEventListener('click', () => copyCode(button));
  });

  async function copyCode(button) {
    const pre = button.parentElement?.querySelector('pre');
    const text = pre?.textContent || '';
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch (_) {}
    if (!copied) {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.append(area);
      area.select();
      try { copied = document.execCommand('copy'); } catch (_) {}
      area.remove();
    }
    const original = button.textContent;
    button.textContent = copied ? '已复制' : '请手动复制';
    window.setTimeout(() => { button.textContent = original; }, 1400);
  }

  const searchButton = document.getElementById('search-open');
  searchButton?.addEventListener('click', showSearch);
  let dialog = null;
  let searchInput = null;
  let searchResults = null;
  let searchItems = [];
  let selectedIndex = -1;
  let previousFocus = null;

  function showSearch() {
    if (dialog) {
      searchInput?.focus();
      return;
    }
    previousFocus = document.activeElement;
    dialog = document.createElement('div');
    dialog.className = 'search-dialog';
    dialog.setAttribute('role', 'presentation');
    const box = document.createElement('div');
    box.className = 'search-box';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', '搜索课程');
    searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.placeholder = '搜索标题和正文，Esc 关闭';
    searchInput.setAttribute('aria-label', '搜索标题和正文');
    searchResults = document.createElement('div');
    searchResults.className = 'search-results';
    searchResults.setAttribute('role', 'listbox');
    box.append(searchInput, searchResults);
    dialog.append(box);
    document.body.append(dialog);
    dialog.addEventListener('click', event => {
      if (event.target === dialog) closeSearch();
    });
    dialog.addEventListener('keydown', handleSearchKey);
    searchInput.addEventListener('input', renderResults);
    searchInput.focus();
    searchResults.textContent = '正在加载搜索索引…';
    const activeDialog = dialog;
    fetch(new URL('search.json', siteRoot).href)
      .then(response => {
        if (!response.ok) throw new Error('search index unavailable');
        return response.json();
      })
      .then(items => {
        if (dialog !== activeDialog) return;
        searchItems = Array.isArray(items) ? items.filter(item => item && typeof item.url === 'string') : [];
        renderResults();
      })
      .catch(() => {
        if (dialog !== activeDialog) return;
        searchItems = [];
        searchResults.textContent = '搜索暂时不可用，请检查网络或使用章节目录。';
      });
  }

  function renderResults() {
    if (!searchResults) return;
    const query = searchInput.value.trim().toLocaleLowerCase();
    const found = query ? searchItems.filter(item =>
      `${item.title || ''} ${item.text || ''}`.toLocaleLowerCase().includes(query)
    ) : [];
    selectedIndex = -1;
    searchResults.replaceChildren();
    if (!query) {
      searchResults.textContent = '输入关键词开始搜索';
      return;
    }
    if (!found.length) {
      searchResults.textContent = '没有找到匹配章节';
      return;
    }
    found.forEach((item, index) => {
      const link = document.createElement('a');
      link.href = new URL(item.url, siteRoot).href;
      link.setAttribute('role', 'option');
      link.dataset.index = String(index);
      const title = document.createElement('span');
      title.textContent = item.title || item.url;
      const excerpt = document.createElement('small');
      excerpt.textContent = String(item.text || '').replace(/\s+/g, ' ').slice(0, 120);
      link.append(title, excerpt);
      searchResults.append(link);
    });
  }

  function handleSearchKey(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeSearch();
      return;
    }
    const links = searchResults?.querySelectorAll('a') || [];
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!links.length) return;
      event.preventDefault();
      if (selectedIndex < 0) selectedIndex = event.key === 'ArrowDown' ? 0 : links.length - 1;
      else selectedIndex = (selectedIndex + (event.key === 'ArrowDown' ? 1 : links.length - 1)) % links.length;
      links[selectedIndex].focus();
      return;
    }
    if (event.key === 'Enter' && links.length) {
      event.preventDefault();
      links[selectedIndex >= 0 ? selectedIndex : 0].click();
    }
  }

  function closeSearch() {
    if (!dialog) return;
    dialog.remove();
    dialog = null;
    searchInput = null;
    searchResults = null;
    searchItems = [];
    selectedIndex = -1;
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    previousFocus = null;
  }

  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      showSearch();
    } else if (event.key === 'Escape') {
      if (dialog) closeSearch();
      else if (side?.classList.contains('open')) menuState(false);
    }
  });
})();
