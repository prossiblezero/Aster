"""Ten targeted transfer tasks; prompts and assertions are fixed before model runs.

Teaching explicitly asks the agent to remember. No memory entries are injected.
Ordinary project files, including any agent-written notes, survive new sessions.
"""

CASES = [
    {
        "id": "01_timezone", "focus": "user_convention",
        "files": {"report.py": "def report_time(iso_timestamp):\n    raise NotImplementedError\n"},
        "learn": ["项目报表的约定：所有时间使用 Asia/Shanghai，格式 YYYY-MM-DD HH:MM。请记住；这一轮不需要实现 report.py。"],
        "reuse": "实现 report.py 的 report_time(iso_timestamp)，输入是带时区的 ISO 时间字符串，输出遵循本项目报表约定。请验证实现。",
        "setup": "from report import report_time as f",
        "checks": {
            "timezone": "assert f('2026-01-01T00:30:00+00:00') == '2026-01-01 08:30'",
            "day_boundary": "assert f('2026-01-01T23:30:00+00:00') == '2026-01-02 07:30'",
            "explicit_offset": "assert f('2026-07-01T10:00:00+02:00') == '2026-07-01 16:00'",
        },
    },
    {
        "id": "02_money", "focus": "user_convention",
        "files": {"billing.py": "def invoice_amount(value):\n    raise NotImplementedError\n"},
        "learn": ["项目发票金额约定：输入十进制字符串，使用 Decimal 从原字符串构造；向零截断到两位小数（ROUND_DOWN），输出固定两位的字符串。负数也是向零截断。请记住，不必实现 billing.py。"],
        "reuse": "实现 billing.py 的 invoice_amount(value)，按照项目发票金额约定处理字符串输入，并验证。",
        "setup": "from billing import invoice_amount as f",
        "checks": {
            "truncate": "assert f('12.999') == '12.99'",
            "negative": "assert f('-12.999') == '-12.99'",
            "decimal_precision": "assert f('9007199254740993.019') == '9007199254740993.01' and f('2') == '2.00'",
        },
    },
    {
        "id": "03_csv", "focus": "user_convention",
        "files": {"exports.py": "def export_tickets(rows):\n    raise NotImplementedError\n"},
        "learn": ["工单 CSV 导出约定：返回 UTF-8 带 BOM 的 bytes，分隔符是分号；列顺序固定为 ticket_id、summary、owner，输入的其他字段不导出。请记住，这一轮不实现 exports.py。"],
        "reuse": "实现 exports.py 的 export_tickets(rows)，输入字典列表，输出遵循项目工单 CSV 约定的 bytes。请验证。",
        "setup": "import csv, io\nfrom exports import export_tickets as f\nb = f([{'ticket_id':'T1','summary':'中文;问题','owner':'Ada','extra':'omit'}])\nrows = list(csv.reader(io.StringIO(b.decode('utf-8-sig')), delimiter=';'))",
        "checks": {
            "encoding": "assert isinstance(b, bytes) and b.startswith(b'\\xef\\xbb\\xbf')",
            "columns": "assert rows[0] == ['ticket_id', 'summary', 'owner']",
            "escaping": "assert rows[1] == ['T1', '中文;问题', 'Ada']",
        },
    },
    {
        "id": "04_correction", "focus": "user_correction",
        "files": {"retry.py": "def should_retry(status):\n    raise NotImplementedError\n"},
        "learn": [
            "项目 HTTP 重试规则：只对 500、502、503 重试。请记住，不需要实现 retry.py。",
            "更正项目 HTTP 重试规则：只对 429、502、503 重试，500 已不再允许。请用新规则替换旧规则，不需要实现 retry.py。",
        ],
        "reuse": "实现 retry.py 的 should_retry(status)，返回 bool，遵循项目最新 HTTP 重试规则，并验证。",
        "setup": "from retry import should_retry as f",
        "checks": {
            "new_rule": "assert f(429) is True",
            "old_rule_removed": "assert f(500) is False",
            "unchanged_and_other": "assert f(502) is True and f(503) is True and f(504) is False and f(200) is False",
        },
    },
    {
        "id": "05_document_synthesis", "focus": "document_knowledge",
        "files": {
            "assets.py": "def asset_key(name, revision):\n    raise NotImplementedError\n",
            "docs/names.md": "Asset names: trim outer whitespace, lowercase, replace each run of whitespace or hyphens with one underscore. Preserve other characters.\n",
            "docs/storage.md": "Asset storage keys have prefix assets/v{revision}/ and extension .bin.\n",
            "docs/revisions.md": "Asset revisions must be positive integers. Invalid revisions raise ValueError.\n",
        },
        "learn": ["阅读 docs/ 下三份资产规范，记住可复用的 asset key 构造规则。本轮只学习，不必实现 assets.py。"],
        "reuse": "实现 assets.py 的 asset_key(name, revision)，遵循本项目资产规范，并验证正常和非法输入。",
        "setup": "from assets import asset_key as f",
        "checks": {
            "combined_convention": "assert f('  Sales--  Report ', 3) == 'assets/v3/sales_report.bin'",
            "preserve_other_chars": "assert f('报告.Q4', 1) == 'assets/v1/报告.q4.bin'",
            "invalid_revision": "for value in (0, -1, 1.5, '2'):\n    try: f('report', value)\n    except ValueError: pass\n    else: raise AssertionError(value)",
        },
    },
    {
        "id": "06_stale_source", "focus": "source_refresh",
        "files": {
            "client.py": "def client_options():\n    raise NotImplementedError\n",
            "docs/client.md": "Client timeout is 8 seconds; retry count is 2. client_options returns a dict with timeout and retries.\n",
        },
        "learn": ["阅读并记住 docs/client.md 的客户端约定。本轮不实现 client.py。"],
        "reuse_files": {"docs/client.md": "Client timeout is 13 seconds; retry count is 0. client_options returns a dict with timeout and retries.\n"},
        "reuse": "实现 client.py 的 client_options()，按项目当前客户端规范返回配置，并验证。",
        "setup": "from client import client_options as f\nvalue = f()",
        "checks": {
            "new_timeout": "assert value['timeout'] == 13",
            "new_retry_count": "assert value['retries'] == 0",
            "schema": "assert set(value) == {'timeout', 'retries'}",
        },
    },
    {
        "id": "07_repair_transfer", "focus": "procedure_transfer", "learn_edit": True,
        "files": {
            "payments.py": "def payment_cents(record):\n    return round(float(record['amount']) * 100)\n",
            "test_payments.py": "import unittest\nfrom payments import payment_cents\nclass PaymentTests(unittest.TestCase):\n    def test_legacy_precedence(self):\n        self.assertEqual(payment_cents({'amount_minor': 123, 'amount': '9.99'}), 123)\n    def test_decimal_rounding(self):\n        self.assertEqual(payment_cents({'amount': '1.005'}), 101)\n    def test_negative(self):\n        self.assertEqual(payment_cents({'amount': '-1.005'}), -101)\n",
        },
        "learn": ["修复 payments.py，使 test_payments.py 通过。运行验证，并记住本项目迁移支付数据的处理经验，供后续相关任务使用。"],
        "reuse_files": {"refunds.py": "def refund_cents(records):\n    return sum(round(float(r['amount']) * 100) for r in records)\n"},
        "reuse": "退款数据复用项目支付记录格式。修复 refunds.py 的 refund_cents(records)，返回所有记录的整数分总和，并验证。",
        "setup": "from refunds import refund_cents as f",
        "checks": {
            "legacy_precedence": "assert f([{'amount_minor': 123, 'amount': '9.99'}, {'amount_minor': 0}]) == 123",
            "decimal_rounding": "assert f([{'amount': '1.005'}, {'amount': '-1.005'}, {'amount': '2.005'}]) == 201",
            "empty_input": "assert f([]) == 0",
        },
    },
    {
        "id": "08_test_recipe", "focus": "procedure_transfer", "learn_edit": True,
        "files": {
            "src/names.py": "def normalize_name(value):\n    return value.lower().replace(' ', '-')\n",
            "checks/test_names.py": "import unittest\nfrom names import normalize_name\nclass NameTests(unittest.TestCase):\n    def test_name(self):\n        self.assertEqual(normalize_name('  Ada   LOVELACE  '), 'ada_lovelace')\n    def test_empty(self):\n        self.assertEqual(normalize_name('   '), '')\n",
        },
        "learn": ["修复 src/names.py，使 checks/test_names.py 通过；找到可用的测试命令，记住项目验证办法和名字规范化经验。"],
        "reuse_files": {
            "src/owners.py": "def normalize_owner(value):\n    return value.lower().replace(' ', '-')\n",
            "checks/test_owners.py": "import unittest\nfrom owners import normalize_owner\nclass OwnerTests(unittest.TestCase):\n    def test_owner(self):\n        self.assertEqual(normalize_owner('  Grace   HOPPER '), 'grace_hopper')\n",
        },
        "reuse": "修复 src/owners.py 的 normalize_owner(value)，应遵循项目名字规范化规则。使用本项目的测试办法验证。",
        "setup": "import sys\nsys.path.insert(0, 'src')\nfrom owners import normalize_owner as f",
        "checks": {
            "normalization": "assert f('  Grace   HOPPER  ') == 'grace_hopper'",
            "all_whitespace": "assert f('Alan\\t  Turing') == 'alan_turing'",
            "empty": "assert f('   ') == ''",
        },
    },
    {
        "id": "09_scope", "focus": "scope_separation",
        "files": {"timestamps.py": "def format_timestamp(iso_timestamp, target):\n    raise NotImplementedError\n"},
        "learn": ["请记住两条有不同适用范围的时间规范：analytics 报表使用 Asia/Shanghai；public_api 输出使用 UTC。两者都用 YYYY-MM-DD HH:MM 字符串。不要把报表的时区推广到 public_api。本轮不实现 timestamps.py。"],
        "reuse": "实现 timestamps.py 的 format_timestamp(iso_timestamp, target)，target 是 analytics 或 public_api，输入是带时区的 ISO 字符串。输出遵循各自的项目时间规范；未知 target 抛 ValueError。请验证。",
        "setup": "from timestamps import format_timestamp as f",
        "checks": {
            "analytics_scope": "assert f('2026-03-01T00:00:00+00:00', 'analytics') == '2026-03-01 08:00'",
            "api_scope": "assert f('2026-03-01T08:00:00+08:00', 'public_api') == '2026-03-01 00:00'",
            "unknown_scope": "try: f('2026-03-01T00:00:00+00:00', 'other')\nexcept ValueError: pass\nelse: raise AssertionError('unknown target accepted')",
        },
    },
    {
        "id": "10_feedback_isolation", "focus": "feedback_attribution",
        "files": {
            "project.md": "Project narrative summaries use French. This convention concerns prose, not string processing behavior.\n",
            "cleaning.py": "def normalize_spaces(value):\n    return value.strip()\n",
            "test_cleaning.py": "import unittest\nfrom cleaning import normalize_spaces\nclass CleaningTests(unittest.TestCase):\n    def test_spaces(self):\n        self.assertEqual(normalize_spaces('  one   two  '), 'one two')\n",
        },
        "learn": ["Read project.md and remember the project convention. Do not implement cleaning.py yet."],
        "reuse": "Fix this project's cleaning.py normalize_spaces(value): collapse all whitespace runs to one space and trim the ends. Verify the code. The project prose convention is unrelated to this function's behavior.",
        "setup": "from cleaning import normalize_spaces as f",
        "checks": {
            "collapse_spaces": "assert f('  one   two  ') == 'one two'",
            "all_whitespace": "assert f('one\\ttwo\\nthree') == 'one two three'",
            "empty": "assert f(' \\t\\n') == ''",
        },
    },
]
