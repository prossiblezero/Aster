import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
LESSONS = SITE / "lessons"
COURSE = REPO / "docs" / "learning"
MARKDOWN = sorted(COURSE.glob("[0-1][0-9]-*.md"))
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
NODE = re.compile(r"(?P<id>[A-Za-z_][\w-]*)\s*(?P<shape>\[[^\]]*\]|\{[^}]*\}|\([^)]*\))")
EDGE = re.compile(
    r"(?P<a>[A-Za-z_][\w-]*)\s*(?:-->|---|-.->)(?:\|(?P<label>[^|]*)\|)?\s*(?P<b>[A-Za-z_][\w-]*)"
)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.urls = []
        self.article_depth = 0
        self.article_text = []
        self.code_frames = []
        self._frame_depth = 0
        self._frame_pre_depth = 0
        self._frame_text = []
        self.diagrams = []
        self._diagram = None
        self._diagram_depth = 0
        self._diagram_code_depth = 0
        self._edge = None
        self._edge_depth = 0
        self._span = None
        self._details_depth = 0
        self.answer_details = 0
        self._summary_depth = 0
        self._summary_text = []
        self.all_text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        for name in ("href", "src"):
            if name in values:
                self.urls.append((name, values[name]))
        classes = set(values.get("class", "").split())
        if tag == "article":
            self.article_depth += 1
        elif self.article_depth:
            self.article_depth += 1
        if "code-frame" in classes:
            self._frame_depth = 1
            self._frame_text = []
        elif self._frame_depth:
            self._frame_depth += 1
        if self._frame_depth and tag == "pre":
            self._frame_pre_depth = 1
        elif self._frame_pre_depth:
            self._frame_pre_depth += 1
        if "diagram" in classes:
            self._diagram_depth = 1
            self._diagram = {"nodes": [], "edges": [], "raw": ""}
        elif self._diagram_depth:
            self._diagram_depth += 1
        if "diagram-edge" in classes:
            self._edge_depth = 1
            self._edge = {"nodes": [], "label": ""}
        elif self._edge_depth:
            self._edge_depth += 1
        if tag == "span" and self._edge is not None:
            if "diagram-node" in classes:
                self._span = ("node", values.get("data-node", ""))
            elif "edge-label" in classes:
                self._span = ("label", "")
        if tag == "details":
            self._details_depth += 1
        if tag == "summary" and self._details_depth:
            self._summary_depth = 1
            self._summary_text = []
        if self._diagram is not None and self._details_depth and tag == "code":
            self._diagram_code_depth = 1

    def handle_endtag(self, tag):
        if tag == "summary" and self._summary_depth:
            if "参考答案" in "".join(self._summary_text):
                self.answer_details += 1
            self._summary_depth = 0
        if tag == "details" and self._details_depth:
            self._details_depth -= 1
        if tag == "span":
            self._span = None
        if self._edge_depth:
            self._edge_depth -= 1
            if not self._edge_depth and self._edge is not None:
                self._diagram["edges"].append(self._edge)
                self._edge = None
        if self._diagram_code_depth and tag == "code":
            self._diagram_code_depth = 0
        if self._diagram_depth:
            self._diagram_depth -= 1
            if not self._diagram_depth and self._diagram is not None:
                self.diagrams.append(self._diagram)
                self._diagram = None
        if self._frame_pre_depth:
            self._frame_pre_depth -= 1
        if self._frame_depth:
            self._frame_depth -= 1
            if not self._frame_depth:
                self.code_frames.append("".join(self._frame_text))
        if self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data):
        self.all_text.append(data)
        if self.article_depth:
            self.article_text.append(data)
        if self._frame_pre_depth:
            self._frame_text.append(data)
        if self._diagram_code_depth:
            self._diagram["raw"] += data
        if self._summary_depth:
            self._summary_text.append(data)
        if self._edge is not None and self._span:
            kind, node_id = self._span
            if kind == "node" and (not self._edge["nodes"] or self._edge["nodes"][-1][0] != node_id):
                self._edge["nodes"].append((node_id, data))
            elif kind == "label":
                self._edge["label"] += data


def parse_page(path):
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def site_pages():
    # Historical evidence under artifacts/ contains old sites with their original links.
    return [
        SITE / "index.html",
        *sorted(LESSONS.glob("*.html")),
        *sorted((SITE / "source").rglob("*.html")),
    ]


def markdown_blocks(path):
    blocks = []
    for info, body in FENCE.findall(path.read_text(encoding="utf-8")):
        language = info.strip().split()[0] if info.strip() else "text"
        blocks.append((language, body))
    return blocks


def diagram_contract(raw):
    nodes = [(match.group("id"), match.group("shape")[1:-1].strip()) for match in NODE.finditer(raw)]
    node_ids = {node_id for node_id, _ in nodes}
    normalized = NODE.sub(lambda match: match.group("id"), raw)
    edges = []
    for line in normalized.splitlines():
        for match in EDGE.finditer(line):
            a, b = match.group("a"), match.group("b")
            if a in node_ids and b in node_ids:
                edges.append((a, b, (match.group("label") or "").strip()))
    return nodes, edges


def test_all_sixteen_lessons_have_real_content():
    pages = sorted(LESSONS.glob("*.html"))
    assert len(MARKDOWN) == len(pages) == 16
    for source, page in zip(MARKDOWN, pages, strict=True):
        assert page.stem == source.stem
        parsed = parse_page(page)
        assert len("".join(parsed.article_text).strip()) > 1800, page
        assert "Aster" in "".join(parsed.all_text), page


def test_every_html_reference_and_anchor_is_valid():
    pages = site_pages()
    assert pages
    parsed_pages = {page.resolve(): parse_page(page) for page in pages}
    for page, parsed in parsed_pages.items():
        duplicates = [value for value, count in Counter(parsed.ids).items() if count > 1]
        assert not duplicates, (page, duplicates)
        for attribute, raw in parsed.urls:
            parts = urlsplit(raw)
            assert not raw.startswith("/"), (page, attribute, raw)
            if parts.scheme or raw.startswith("//"):
                continue
            path_part = unquote(parts.path)
            target = (page.parent / path_part).resolve() if path_part else page
            assert target == SITE.resolve() or SITE.resolve() in target.parents, (page, raw, target)
            assert target.is_file(), (page, raw, target)
            if parts.fragment:
                assert target.suffix == ".html", (page, raw)
                target_page = parsed_pages.get(target) or parse_page(target)
                assert unquote(parts.fragment) in target_page.ids, (page, raw)


def test_original_code_blocks_and_answers_are_preserved():
    expected = []
    answers = 0
    actual = []
    actual_answers = 0
    for source in MARKDOWN:
        expected.extend(body for language, body in markdown_blocks(source) if language != "mermaid")
        answers += source.read_text(encoding="utf-8").count("<summary>参考答案</summary>")
        page = parse_page(LESSONS / f"{source.stem}.html")
        actual.extend(page.code_frames)
        actual_answers += page.answer_details
    assert len(expected) == len(actual) == 79
    assert actual == expected
    assert answers == actual_answers == 8


def test_each_mermaid_diagram_preserves_nodes_edges_labels_and_order():
    expected = []
    actual = []
    for source in MARKDOWN:
        for language, body in markdown_blocks(source):
            if language == "mermaid":
                nodes, edges = diagram_contract(body)
                expected.append((nodes, edges, body))
        for diagram in parse_page(LESSONS / f"{source.stem}.html").diagrams:
            edges = [
                (edge["nodes"][0][0], edge["nodes"][1][0], edge["label"].strip())
                for edge in diagram["edges"]
                if len(edge["nodes"]) == 2
            ]
            nodes, _ = diagram_contract(diagram["raw"])
            actual.append((nodes, edges, diagram["raw"]))
    assert len(expected) == len(actual) == 11
    assert sum(len(nodes) for nodes, _, _ in expected) == 101
    assert sum(len(edges) for _, edges, _ in expected) == 100
    assert actual == expected


def test_site_specific_contracts():
    package = SITE / "source" / "src" / "aster"
    source = package / "core" / "runtime.py.html"
    assert source.is_file()
    assert {path.name for path in package.glob("*.py.html")} == {
        "__init__.py.html", "cli.py.html", "config.py.html", "schema.py.html", "messages.py.html",
    }
    parsed_source = parse_page(source)
    assert "L1" in parsed_source.ids and any(url == "#L1" for _, url in parsed_source.urls)
    quickstart = (LESSONS / "00-quickstart.html").read_text(encoding="utf-8")
    assert "../source/pyproject.toml" in quickstart
    assert "../index.html" in quickstart
    assert "source/docs/learning/README.md" not in quickstart
    assert "Markdown 原文下载" in "".join(
        text
        for page in sorted(LESSONS.glob("*.html"))
        for text in parse_page(page).all_text
    )
    generated = "\n".join(page.read_text(encoding="utf-8") for page in site_pages())
    assert "REPLACE_LESSONS" not in generated
    assert not re.search(r"\{\{[^}]+\}\}|\$\{[^}]+\}", generated)


def test_navigation_titles_do_not_repeat_chapter_numbers():
    index = (SITE / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'<b>(\d{2})</b><span>\1\s*(?:[｜·])?', index)
    for page in LESSONS.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r'<span>(\d{2})</span>\1\s*(?:[｜·])?', text)
