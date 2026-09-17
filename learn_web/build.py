from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
LEARNING = ROOT / "docs" / "learning"
FILES = sorted(LEARNING.glob("[0-1][0-9]-*.md"))
NAMES = [path.stem for path in FILES]
TITLES = [path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ") for path in FILES]
LOCAL_LINK = re.compile(r"(?<!!)\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
NODE = re.compile(r"(?P<id>[A-Za-z_][\w-]*)\s*(?P<shape>\[[^\]]*\]|\{[^}]*\}|\([^)]*\))")
EDGE = re.compile(r"(?P<a>[A-Za-z_][\w-]*)\s*(?:-->|---|-.->)(?:\|(?P<label>[^|]*)\|)?\s*(?P<b>[A-Za-z_][\w-]*)")


def slug(text: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff -]", "", text.lower()).strip().replace(" ", "-")
    return re.sub(r"-+", "-", value) or "section"


def rel_url(page: Path, target: Path, fragment: str = "") -> str:
    return Path(os.path.relpath(target, page.parent)).as_posix() + fragment


def display_title(title: str) -> str:
    """Remove a leading chapter number without changing the course H1."""
    return re.sub(r"^\s*\d{2}\s*(?:[｜·]\s*)?", "", title).strip()


def resolve_markdown_link(raw: str) -> Path | None:
    path = unquote(urlsplit(raw).path)
    if not path or urlsplit(raw).scheme or raw.startswith(("#", "//")):
        return None
    candidate = (LEARNING / path).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def published_target(source: Path) -> Path:
    if source.parent == LEARNING and source.suffix == ".md" and source.stem in NAMES:
        return OUT / "lessons" / f"{source.stem}.html"
    target = OUT / "source" / source.relative_to(ROOT)
    return target.with_suffix(target.suffix + ".html") if source.suffix == ".py" else target


def source_link(href: str, page: Path) -> str:
    parts = urlsplit(href)
    source = resolve_markdown_link(href)
    if source is None:
        return href
    if source == LEARNING / "README.md":
        return rel_url(page, OUT / "index.html")
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return rel_url(page, published_target(source), fragment)


def rewrite_link(match: re.Match, page: Path) -> str:
    label, href = match.group(1), match.group(2)
    source = resolve_markdown_link(href)
    if source is not None and source.suffix == ".md" and source.parent != LEARNING:
        label += "（Markdown 原文下载）"
    return f"[{label}]({source_link(href, page)})"


def referenced_files() -> set[Path]:
    refs = {ROOT / "pyproject.toml"}
    for lesson in FILES:
        for href in LOCAL_LINK.findall(lesson.read_text(encoding="utf-8")):
            source = resolve_markdown_link(href)
            if source is not None and source != LEARNING / "README.md" and not (
                source.parent == LEARNING and source.stem in NAMES
            ):
                refs.add(source)
    # Product source is small and is the subject of the course, so keep it browsable as a unit.
    refs.update(path for path in (ROOT / "src" / "aster").rglob("*.py") if path.is_file())
    return refs


def render_source_page(source: Path, target: Path) -> None:
    lexer_name = "python" if source.suffix == ".py" else "text"
    lexer = get_lexer_by_name(lexer_name)
    lines = source.read_text(encoding="utf-8").splitlines()
    rendered = []
    for number, line in enumerate(lines, 1):
        code = highlight(line + "\n", lexer, HtmlFormatter(nowrap=True))
        rendered.append(
            f'<span class="line" id="L{number}"><a href="#L{number}" aria-label="第 {number} 行">{number}</a>'
            f"<code>{code}</code></span>"
        )
    css = rel_url(target, OUT / "assets" / "site.css")
    home = rel_url(target, OUT / "index.html")
    original = source.relative_to(ROOT).as_posix()
    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(original)} · Aster 源码</title>"
        f'<link rel="stylesheet" href="{css}"></head><body class="source-page">'
        f'<header><a href="{home}">Aster 教程</a><span>原始相对路径：{html.escape(original)}</span></header>'
        f'<main><h1>{html.escape(source.name)}</h1><pre class="source-code">{"".join(rendered)}</pre></main>'
        "</body></html>"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")


def parse_mermaid(raw: str) -> str:
    nodes = {match.group("id"): match.group("shape")[1:-1].strip() for match in NODE.finditer(raw)}
    edges: list[tuple[str, str, str]] = []
    # Remove inline node definitions before parsing arrows: A[text] --> B[text].
    normalized = NODE.sub(lambda match: match.group("id"), raw)
    for line in normalized.splitlines():
        for match in EDGE.finditer(line):
            a, b = match.group("a"), match.group("b")
            if a in nodes and b in nodes:
                edges.append((a, b, (match.group("label") or "").strip()))
    if not nodes or not edges:
        raise ValueError(f"unsupported Mermaid diagram:\n{raw}")
    edge_html = "".join(
        '<li class="diagram-edge">'
        f'<span class="diagram-node" data-node="{html.escape(a)}">{html.escape(nodes[a])}</span>'
        f'<span class="edge-arrow" aria-hidden="true">→</span>'
        + (f'<span class="edge-label">{html.escape(label)}</span>' if label else "")
        + f'<span class="diagram-node" data-node="{html.escape(b)}">{html.escape(nodes[b])}</span></li>'
        for a, b, label in edges
    )
    labels = "".join(
        f'<span class="diagram-key"><code>{html.escape(key)}</code> {html.escape(value)}</span>'
        for key, value in nodes.items()
    )
    return (
        f'<figure class="diagram" data-edge-count="{len(edges)}"><ol class="diagram-edges">{edge_html}</ol>'
        f'<div class="diagram-key-list" aria-label="图中节点">{labels}</div>'
        '<figcaption>每一行忠实对应原图的一条边；回到前置节点的边表示循环。</figcaption>'
        f'<details><summary>查看原始 Mermaid 定义</summary><pre><code>{html.escape(raw)}</code></pre></details></figure>\n'
    )


def render_fence(token) -> str:
    info = token.info.strip().split()
    lang = info[0] if info else "text"
    raw = token.content
    if lang == "mermaid":
        return parse_mermaid(raw)
    try:
        formatted = highlight(raw, get_lexer_by_name(lang), HtmlFormatter(nowrap=False))
    except ClassNotFound:
        formatted = f"<pre><code>{html.escape(raw)}</code></pre>"
    return (
        '<div class="code-frame"><button class="copy-code" title="复制代码" '
        f'aria-label="复制代码">复制</button>{formatted}</div>\n'
    )


def unique_heading_ids(body: str) -> str:
    used: dict[str, int] = {}

    def replace(match: re.Match) -> str:
        base = slug(re.sub("<[^>]+>", "", match.group(2)))
        used[base] = used.get(base, 0) + 1
        value = base if used[base] == 1 else f"{base}-{used[base]}"
        return f'<h{match.group(1)} id="{value}">{match.group(2)}</h{match.group(1)}>'

    return re.sub(r"<h([1-6])>(.*?)</h\1>", replace, body)


def render_index() -> None:
    rows = "".join(
        f'<a class="lesson-row" href="lessons/{path.stem}.html"><b>{i:02}</b><span>'
        f'{html.escape(display_title(TITLES[i]))}<small>进入本章 →</small></span></a>'
        for i, path in enumerate(FILES)
    )
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="沿真实源码学习 Coding Agent Harness 的中文课程"><title>Aster · 工程阅读课程</title><link rel="stylesheet" href="assets/site.css"></head><body data-root="."><header class="topbar"><a class="brand" href="index.html"><b>A</b> Aster <span>工程阅读</span></a><nav><button id="search-open" aria-label="搜索课程" title="搜索">⌕</button><button id="theme-toggle" aria-label="切换明暗主题" title="切换主题">◐</button></nav></header><main class="home"><div class="home-kicker">CODING AGENT HARNESS / 课程 00–15</div><h1>跟着一次任务，读懂 Aster。</h1><p class="lead">面向会 Python、尚未系统理解 Coding Agent Harness 的读者。沿真实 Runtime 和工具源码，拆开一次任务如何被观察、执行、验证、恢复和评测。</p><div class="home-actions"><a class="button" href="lessons/00-quickstart.html">从第 00 章开始 →</a><a class="button secondary" id="continue-link" href="lessons/00-quickstart.html">查看学习路线</a></div><section class="home-section"><h2>你需要什么，最后会得到什么</h2><div class="route-grid"><div class="route-card"><h3>前提</h3><p>能读 Python 函数、类和字典。遇到 async，只需先理解为可能等待模型、进程或用户。</p></div><div class="route-card"><h3>读法</h3><p>每章从具体问题出发，沿当前源码看数据流，再运行离线实验，最后回答练习中的为什么。</p></div><div class="route-card"><h3>成果</h3><p>能够解释工具调用、权限沙箱、上下文治理、证据归档、记忆、恢复和评测边界。</p></div></div></section><section class="home-section"><h2>16 章学习路线</h2><div class="lesson-grid">{rows}</div></section><section class="home-section"><h2>三条专题路线</h2><div class="route-list"><div class="route-card"><h3>上下文</h3><p>01 → 02 → 05 → 06 → 07 → 14<br>从模型协议走到预算、归档和实验设计。</p></div><div class="route-card"><h3>记忆</h3><p>01 → 05 → 10 → 11 → 14<br>区分会话工作记忆、长期证据与演化反馈。</p></div><div class="route-card"><h3>安全与可靠性</h3><p>01 → 03 → 04 → 08 → 09 → 13<br>从工具边界走到审批、恢复、中断与 Worker。</p></div></div></section><section class="home-section"><h2>从请求到工具结果</h2><p>Aster 让不同入口共享一条执行路径：模型或外部策略提出动作，Runtime 组织上下文并检查权限，工具接触工作区，结果成为下一轮的证据。</p><div class="arch-diagram"><div class="arch-step">用户任务<br><small>TaskSpec</small></div><div class="arch-arrow">→</div><div class="arch-step">Runtime<br><small>上下文 / 权限</small></div><div class="arch-arrow">→</div><div class="arch-step">ToolRegistry<br><small>读取 / 修改 / 测试</small></div><div class="arch-arrow">→</div><div class="arch-step">结果与状态<br><small>Evidence / RunState</small></div><div class="arch-arrow">→</div><div class="arch-step">验证与回答<br><small>Verifier</small></div></div><p class="home-footer">本课程覆盖已实现的 Harness 和选读环境接口。项目目前没有实现 SFT、GRPO 或其他训练算法；脚本动作回放、工程测试与真实模型评测保持区分。</p></section></main><script src="assets/site.js" defer></script></body></html>'''
    (OUT / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    if len(FILES) != 16:
        raise RuntimeError(f"expected 16 lessons, found {len(FILES)}")
    (OUT / "lessons").mkdir(exist_ok=True)
    shutil.rmtree(OUT / "source", ignore_errors=True)
    (OUT / "source").mkdir()

    for source in sorted(referenced_files()):
        target = published_target(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".py":
            render_source_page(source, target)
        else:
            shutil.copy2(source, target)

    md = MarkdownIt("commonmark", {"html": True, "linkify": True}).enable("table").enable("strikethrough")
    md.add_render_rule("fence", lambda renderer, tokens, idx, options, env: render_fence(tokens[idx]))
    groups = [
        ("基础", range(0, 3)),
        ("可靠执行", range(3, 5)),
        ("状态与上下文", range(5, 10)),
        ("记忆与扩展", range(10, 16)),
    ]
    for i, source in enumerate(FILES):
        page_path = OUT / "lessons" / f"{source.stem}.html"
        text = source.read_text(encoding="utf-8")
        text = re.sub(
            r"\[([^]]+)\]\(([^)]+)\)",
            lambda match: rewrite_link(match, page_path),
            text,
        )
        body = unique_heading_ids(md.render(text))
        headings = re.findall(r'<h[23] id="([^"]+)">(.*?)</h[23]>', body)
        toc = "".join(
            f'<a href="#{anchor}">{html.escape(re.sub("<[^>]+>", "", title))}</a>'
            for anchor, title in headings
        )
        sidebar = "".join(
            f'<p class="nav-label">{label}</p><ol>'
            + "".join(
                f'<li><a class="{"current" if j == i else ""}" href="{NAMES[j]}.html">'
                f'<span>{j:02}</span>{html.escape(display_title(TITLES[j]))}</a></li>'
                for j in indexes
            )
            + "</ol>"
            for label, indexes in groups
        )
        note = ""
        if i == 4:
            note = '<aside class="course-note"><strong>实现核对</strong>read_file 记录规范化文本哈希；Runtime 在审批前固定原始字节哈希（新文件记为不存在），并在审批等待结束后的执行前、隔离复制后、best_effort 降级前复核。沙箱写回前还会对照执行前快照检查并发漂移。Git 仓库的常规扫描、快照和输入复制排除被忽略的未跟踪产物；显式路径仍可读，但写入与写回会拒绝。无 .git 副本和非 Git 工作区都不解析 Git ignore；命令新生成的 ignored 路径会在原仓库发布检查时整批拒绝。这些保护不是跨进程事务。</aside>'
        elif i == 13:
            note = '<aside class="course-note"><strong>实现核对</strong>当前 CLI 和 TUI 每次提交都会调用 Runtime 启动一个新任务；checkpoint 只恢复某一次运行的状态，不等于持续聊天会话。Memory 与 Dream 是确定性的证据维护和排序规则，不是训练或自主学习模型。</aside>'
        prev_link = (
            f'<a href="{NAMES[i - 1]}.html">← {html.escape(TITLES[i - 1])}</a>'
            if i
            else '<a href="../index.html">← 课程首页</a>'
        )
        next_link = (
            f'<a href="{NAMES[i + 1]}.html">{html.escape(TITLES[i + 1])} →</a>'
            if i + 1 < len(FILES)
            else '<a href="../index.html">回到课程首页 →</a>'
        )
        page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(TITLES[i])} · Aster 教程</title><link rel="stylesheet" href="../assets/site.css"></head><body data-lesson="{NAMES[i]}" data-root=".."><header class="topbar"><a class="brand" href="../index.html"><b>A</b> Aster <span>工程阅读</span></a><nav><button id="search-open" aria-label="搜索课程" title="搜索">⌕</button><button id="theme-toggle" aria-label="切换明暗主题" title="切换主题">◐</button><button id="menu-toggle" aria-label="展开章节目录" title="章节目录">☰</button></nav></header><div class="reading-layout"><aside class="sidebar"><a class="home-link" href="../index.html">← 课程首页</a>{sidebar}<div class="continue"><small>学习进度</small><a id="continue-link" href="00-quickstart.html">继续上次阅读 →</a></div></aside><main class="article"><div class="article-meta">ASTER / 学习课程 / {i:02}</div><article><div class="lesson-toolbar"><label><input type="checkbox" class="completed-toggle"> 标记本章完成</label><span class="progress-text"></span></div>{note}{body}<nav class="chapter-nav">{prev_link}{next_link}</nav></article></main><aside class="toc"><strong>本页目录</strong>{toc}</aside></div><div class="mobile-overlay"></div><script src="../assets/site.js" defer></script></body></html>'''
        page_path.write_text(page, encoding="utf-8")

    render_index()
    search = [
        {
            "url": f"lessons/{path.stem}.html",
            "title": TITLES[i],
            "text": re.sub(r"[`#>*|]", " ", path.read_text(encoding="utf-8")),
        }
        for i, path in enumerate(FILES)
    ]
    (OUT / "search.json").write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
