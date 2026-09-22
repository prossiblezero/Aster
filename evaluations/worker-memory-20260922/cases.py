"""Frozen small maintenance tasks; reference implementations never reach the agent."""

from textwrap import dedent


def code(text):
    return dedent(text).strip() + "\n"


def module(path, spec, reference, checks):
    return {"path": path, "spec": spec, "reference": code(reference), "checks": code(checks)}


WORKERS = [
    (
        "csv-ledger",
        "independent",
        [
            module(
                "ledger_import.py",
                "Implement parse_csv(text): CSV header id,amount; ignore blank rows; trim ids, reject empty/duplicate ids and missing/extra columns with ValueError. Parse finite amounts with Decimal, round half up to integer cents. Return list of {'id':str,'cents':int}, preserving input order. Quoted ids may contain commas.",
                """
            import csv, io
            from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
            def parse_csv(text):
                rows = csv.reader(io.StringIO(text))
                if next(rows, None) != ['id', 'amount']: raise ValueError('header')
                out, seen = [], set()
                for row in rows:
                    if not row or all(not x.strip() for x in row): continue
                    if len(row) != 2 or not row[0].strip() or row[0].strip() in seen: raise ValueError('row')
                    ident = row[0].strip()
                    try:
                        amount = Decimal(row[1].strip())
                        if not amount.is_finite(): raise ValueError('amount')
                        cents = int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    except (InvalidOperation, OverflowError): raise ValueError('amount') from None
                    seen.add(ident)
                    out.append({'id': ident, 'cents': cents})
                return out
        """,
                r"""
            from ledger_import import parse_csv
            assert parse_csv('id,amount\n"a,b",1.005\nz,-1.005\n') == [{'id':'a,b','cents':101},{'id':'z','cents':-101}]
            assert parse_csv('id,amount\n\n') == []
            for text in ['id,amount\na,1\n a ,2', 'id,amount\n,1', 'id,amount\na,NaN', 'id,amount\na,Infinity', 'id,amount\na,1,2','id,amount\na', 'x,y\na,2']:
                with pytest.raises(ValueError): parse_csv(text)
        """,
            ),
            module(
                "ledger_summary.py",
                "Implement summarize(rows): input dictionaries id,cents, no mutation. Group cents by id, return {'totals': [{'id': id,'cents':sum}, ...] sorted lexicographically by id, 'grand_total':int}. Empty input gives empty totals and zero.",
                """
            def summarize(rows):
                totals = {}
                for row in rows: totals[row['id']] = totals.get(row['id'], 0) + row['cents']
                return {'totals':[{'id':k,'cents':totals[k]} for k in sorted(totals)], 'grand_total':sum(totals.values())}
        """,
                r"""
            from ledger_summary import summarize
            rows = [{'id':'b','cents':5},{'id':'a','cents':-2},{'id':'b','cents':7}]
            before = [dict(x) for x in rows]
            assert summarize(rows) == {'totals':[{'id':'a','cents':-2},{'id':'b','cents':12}], 'grand_total':10}
            assert rows == before
            assert summarize([]) == {'totals':[], 'grand_total':0}
        """,
            ),
        ],
    ),
    (
        "access-report",
        "independent",
        [
            module(
                "access_parse.py",
                "Implement parse_lines(text): nonempty lines contain status then nonnegative decimal integer latency_ms, separated by whitespace. Status must be an integer from 100 through 599; malformed lines are skipped and their 1-based physical line numbers returned. Output {'rows':[{'status':int,'latency_ms':int}], 'errors':[int]}. Empty lines are ignored but count for numbering.",
                """
            def parse_lines(text):
                rows, errors = [], []
                for n, line in enumerate(text.splitlines(), 1):
                    if not line.strip(): continue
                    p = line.split()
                    if len(p)!=2 or not all(x.isascii() and x.isdecimal() for x in p) or not 100<=int(p[0])<=599:
                        errors.append(n)
                    else: rows.append({'status':int(p[0]),'latency_ms':int(p[1])})
                return {'rows':rows,'errors':errors}
        """,
                r"""
            from access_parse import parse_lines
            assert parse_lines('200 10\n\nBAD\n503 0\n99 1\n200 -1\n200 1 extra\n') == {'rows':[{'status':200,'latency_ms':10},{'status':503,'latency_ms':0}],'errors':[3,5,6,7]}
            assert parse_lines(' \n') == {'rows':[],'errors':[]}
        """,
            ),
            module(
                "access_stats.py",
                "Implement summarize(rows): report count, server_errors (status >=500), p95_ms using nearest rank ceil(0.95*n) over sorted latency, zero when empty. Do not mutate rows.",
                """
            import math
            def summarize(rows):
                times=sorted(x['latency_ms'] for x in rows)
                return {'count':len(rows),'server_errors':sum(x['status']>=500 for x in rows),'p95_ms':times[math.ceil(.95*len(times))-1] if times else 0}
        """,
                r"""
            from access_stats import summarize
            rows=[{'status':200,'latency_ms':n} for n in range(20,0,-1)]
            rows[0]['status']=503
            before=[dict(x) for x in rows]
            assert summarize(rows)=={'count':20,'server_errors':1,'p95_ms':19}
            assert rows==before
            assert summarize([])=={'count':0,'server_errors':0,'p95_ms':0}
        """,
            ),
        ],
    ),
    (
        "note-export",
        "independent",
        [
            module(
                "note_meta.py",
                "Implement parse_note(text): optional front matter starts on first line with '---' and closes with a line '---'. Header lines are key:value split at first colon, trim both, reject missing colon, empty key, duplicate key or unterminated header with ValueError. Return {'meta':dict,'body':str}, retaining body bytes/newlines as text. Without header meta is empty and body unchanged.",
                """
            def parse_note(text):
                lines=text.splitlines(keepends=True)
                if not lines or lines[0].rstrip('\\r\\n')!='---': return {'meta':{},'body':text}
                meta={}
                for i,line in enumerate(lines[1:],1):
                    if line.rstrip('\\r\\n')=='---': return {'meta':meta,'body':''.join(lines[i+1:])}
                    if ':' not in line: raise ValueError('header')
                    k,v=line.split(':',1); k=k.strip()
                    if not k or k in meta: raise ValueError('key')
                    meta[k]=v.strip()
                raise ValueError('unterminated')
        """,
                r"""
            from note_meta import parse_note
            assert parse_note('---\ntitle: a:b\ntag: x\n---\nHello\n')=={'meta':{'title':'a:b','tag':'x'},'body':'Hello\n'}
            assert parse_note('plain\n')=={'meta':{},'body':'plain\n'}
            for s in ['---\na: 1\na: 2\n---\n','---\na: 1\n','---\nbad\n---\n']:
                with pytest.raises(ValueError): parse_note(s)
        """,
            ),
            module(
                "note_routes.py",
                "Implement routes(titles): ASCII lowercase slug replaces each run of non [a-z0-9] with '-', strips '-', empty becomes 'note'; assign unique slugs in input order by appending -2,-3,... to base, checking against every previously allocated slug including literal numbered titles. Do not mutate input.",
                """
            import re
            def routes(titles):
                used=set(); out=[]
                for title in titles:
                    base=re.sub('[^a-z0-9]+','-',title.lower()).strip('-') or 'note'
                    slug=base; n=2
                    while slug in used: slug=f'{base}-{n}'; n+=1
                    used.add(slug); out.append(slug)
                return out
        """,
                r"""
            from note_routes import routes
            assert routes(['Hello','hello','Hello-2','Hello','你好','!!!'])==['hello','hello-2','hello-2-2','hello-3','note','note-2']
            assert routes([])==[]
        """,
            ),
        ],
    ),
    (
        "artifact-index",
        "independent",
        [
            module(
                "artifact_paths.py",
                "Implement clean_paths(paths): convert backslash to '/', collapse empty and '.' components; reject leading slash, Windows drive prefix, any '..' component, empty normalized result with ValueError. Return unique normalized relative paths sorted; don't mutate input.",
                """
            import re
            def clean_paths(paths):
                out=set()
                for path in paths:
                    path=path.replace('\\\\','/')
                    if path.startswith('/') or re.match(r'^[A-Za-z]:',path): raise ValueError('absolute')
                    parts=path.split('/')
                    if '..' in parts: raise ValueError('parent')
                    parts=[p for p in parts if p not in ('','.')]
                    if not parts: raise ValueError('empty')
                    out.add('/'.join(parts))
                return sorted(out)
        """,
                r"""
            from artifact_paths import clean_paths
            assert clean_paths(['a//b','./a/b','z\\q'])==['a/b','z/q']
            for p in ['/x','../x','a/../b','C:\\x','./']:
                with pytest.raises(ValueError): clean_paths([p])
            assert clean_paths([])==[]
        """,
            ),
            module(
                "artifact_diff.py",
                "Implement diff_index(before,after) for path->hash dictionaries. Return sorted lists under added,removed,changed; same hash unchanged; no mutation.",
                """
            def diff_index(before,after):
                return {'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),'changed':sorted(k for k in before.keys()&after.keys() if before[k]!=after[k])}
        """,
                r"""
            from artifact_diff import diff_index
            a={'a':'1','b':'2','c':'3'}; b={'b':'4','c':'3','d':'5'}
            assert diff_index(a,b)=={'added':['d'],'removed':['a'],'changed':['b']}
            assert a=={'a':'1','b':'2','c':'3'}
            assert diff_index({}, {})=={'added':[],'removed':[],'changed':[]}
        """,
            ),
        ],
    ),
    (
        "dependent-export",
        "dependent",
        [
            module(
                "identity.py",
                "Implement normalize_id(value): strip and casefold text, reject empty or any internal whitespace with ValueError. This public function is shared by the export module.",
                """
            def normalize_id(value):
                value=value.strip().casefold()
                if not value or any(c.isspace() for c in value): raise ValueError('id')
                return value
        """,
                r"""
            from identity import normalize_id
            assert normalize_id(' STRASSE ')==normalize_id('Straße')=='strasse'
            for s in ['',' \t','a b','a\tb']:
                with pytest.raises(ValueError): normalize_id(s)
        """,
            ),
            module(
                "export_rows.py",
                "Implement render_csv(rows) calling identity.normalize_id for every id (do not duplicate its logic). Emit CSV header id,value with LF line endings, preserve row order, escape values using CSV rules, reject normalized duplicate ids with ValueError. Do not mutate input.",
                """
            import csv, io, identity
            def render_csv(rows):
                f=io.StringIO(newline=''); w=csv.writer(f,lineterminator='\\n'); w.writerow(['id','value']); seen=set()
                for r in rows:
                    ident=identity.normalize_id(r['id'])
                    if ident in seen: raise ValueError('duplicate')
                    seen.add(ident); w.writerow([ident,r['value']])
                return f.getvalue()
        """,
                r"""
            import export_rows, identity
            assert export_rows.render_csv([{'id':' A ','value':'x,y'}])=='id,value\na,"x,y"\n'
            assert export_rows.render_csv([])=='id,value\n'
            with pytest.raises(ValueError): export_rows.render_csv([{'id':'X','value':1},{'id':' x ','value':2}])
            import inspect
            assert 'normalize_id' in inspect.getsource(export_rows)
        """,
            ),
        ],
    ),
    (
        "shared-settings",
        "shared_file",
        [
            module(
                "settings.py",
                "Implement both parse_config(text) and retry_delays(config) in the shared settings.py. parse_config reads key=value lines; ignore blank/# comments, reject unknown/duplicate keys, defaults attempts=3/base_ms=40/cap_ms=250, require integer attempts 1..6, base_ms>=0 and cap_ms>=base_ms, otherwise ValueError. retry_delays returns attempts-1 delays min(cap_ms,base_ms*2**i). These functions share the same config contract; don't permit conflicting concurrent edits.",
                """
            def parse_config(text):
                out={'attempts':3,'base_ms':40,'cap_ms':250}; seen=set()
                for line in text.splitlines():
                    line=line.strip()
                    if not line or line.startswith('#'): continue
                    if '=' not in line: raise ValueError('line')
                    k,v=line.split('=',1); k=k.strip()
                    if k not in out or k in seen: raise ValueError('key')
                    out[k]=int(v.strip()); seen.add(k)
                if not 1<=out['attempts']<=6 or out['base_ms']<0 or out['cap_ms']<out['base_ms']: raise ValueError('range')
                return out
            def retry_delays(config):
                return [min(config['cap_ms'],config['base_ms']*2**i) for i in range(config['attempts']-1)]
        """,
                r"""
            from settings import parse_config, retry_delays
            assert parse_config('')=={'attempts':3,'base_ms':40,'cap_ms':250}
            assert retry_delays(parse_config('attempts=6\nbase_ms=40\ncap_ms=100'))==[40,80,100,100,100]
            assert retry_delays(parse_config('attempts=1'))==[]
            for s in ['x=2','attempts=0','attempts=7','base_ms=-1','base_ms=300','attempts=2\nattempts=3','bad']:
                with pytest.raises(ValueError): parse_config(s)
        """,
            ),
        ],
    ),
]


COMMON = (
    "Implement the requested maintenance in this small Python repository. "
    "Use only the standard library. Add meaningful tests and finish by running them with run_tests. "
    "Do not modify README.md or docs/. If Worker tools are available, you may delegate independent "
    "work; choose the decomposition and safe write scopes yourself, wait for results, then verify "
    "the integrated product. Workers have 12 tool steps/12 turns each. Do not just launch and finish. "
    "The whole task has a shared evaluation ceiling of 60 admitted tool actions, 40 model calls "
    "and 500000 reported tokens, and a 600-second deadline.\n"
)


def worker_cases():
    result = []
    for name, category, modules in WORKERS:
        spec = "\n\n".join(m["path"] + ": " + m["spec"] for m in modules)
        result.append(
            {
                "id": name,
                "category": category,
                "modules": modules,
                "files": {
                    "README.md": spec + "\n",
                    **{m["path"]: "# Implementation pending.\n" for m in modules},
                },
                "prompt": COMMON + spec,
            }
        )
    return result


def memory_projects():
    return [
        {
            "id": "parcel",
            "topic": "parcel envelope",
            "docs": {
                "wire.md": "Parcel envelope wire contract: envelope key is parcels, schema field is wire_rev with value 7. Each parcel row has identifier key parcel_ref and weight key grams.\n",
                "identity.md": "Parcel identifiers are normalized by stripping surrounding whitespace and applying casefold. Empty identifiers and internal whitespace are rejected with ValueError. Duplicate normalized identifiers reject the entire parcel envelope with ValueError.\n",
                "weight.md": "Parcel weight input is a decimal string in kilograms. Multiply by 1000 and round half up to integer grams. Reject negative or non-finite inputs with ValueError.\n",
                "order.md": "Parcel envelope rows are sorted lexicographically by normalized parcel_ref. The export must not mutate caller rows. Empty exports still contain the schema field and an empty list.\n",
                "display.md": "Parcel user-facing display labels use title case. Presentation labels never replace persisted parcel identifiers.\n",
            },
            "tasks": [
                module(
                    "parcel_export.py",
                    "Implement export(rows) for the parcel envelope, following this repository's wire, identity, weight and order contracts. Input rows have id and kg strings; output is a dictionary, not JSON text.",
                    """
                    from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
                    def export(rows):
                        out=[]; seen=set()
                        for row in rows:
                            ident=row['id'].strip().casefold()
                            if not ident or any(c.isspace() for c in ident) or ident in seen: raise ValueError('id')
                            seen.add(ident)
                            try:
                                weight=Decimal(row['kg'])
                                if not weight.is_finite() or weight<0: raise ValueError('weight')
                                grams=int((weight*1000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                            except InvalidOperation: raise ValueError('weight') from None
                            out.append({'parcel_ref':ident,'grams':grams})
                        return {'wire_rev':7,'parcels':sorted(out,key=lambda x:x['parcel_ref'])}
                """,
                    r"""
                    from parcel_export import export
                    rows=[{'id':' Z ','kg':'1.0005'},{'id':'a','kg':'0'}]; before=[dict(x) for x in rows]
                    assert export(rows)=={'wire_rev':7,'parcels':[{'parcel_ref':'a','grams':0},{'parcel_ref':'z','grams':1001}]}
                    assert rows==before
                    assert export([])=={'wire_rev':7,'parcels':[]}
                    for rows in [[{'id':'x','kg':'-1'}],[{'id':'x','kg':'NaN'}],[{'id':'a b','kg':'1'}],[{'id':'X','kg':'1'},{'id':' x ','kg':'2'}]]:
                        with pytest.raises(ValueError): export(rows)
                """,
                ),
                module(
                    "parcel_totals.py",
                    "Implement totals(rows) for parcel envelope reporting using the same identity and weight contracts. Input is id/kg rows; duplicate identifiers are allowed here and aggregate their independently rounded grams. Return a dictionary mapping normalized identifier to total integer grams; no mutation.",
                    """
                    from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
                    def totals(rows):
                        out={}
                        for r in rows:
                            k=r['id'].strip().casefold()
                            if not k or any(c.isspace() for c in k): raise ValueError('id')
                            try:
                                d=Decimal(r['kg'])
                                if not d.is_finite() or d<0: raise ValueError('kg')
                                g=int((d*1000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                            except InvalidOperation: raise ValueError('kg') from None
                            out[k]=out.get(k,0)+g
                        return out
                """,
                    r"""
                    from parcel_totals import totals
                    assert totals([{'id':'X','kg':'0.0005'},{'id':' x ','kg':'0.0005'}])=={'x':2}
                    assert totals([])=={}
                    for v in ['NaN','-0.1','Infinity']:
                        with pytest.raises(ValueError): totals([{'id':'x','kg':v}])
                """,
                ),
                module(
                    "parcel_export.py",
                    "The parcel envelope wire contract has been revised in docs/wire.md. Update export(rows) to the revised contract while preserving identity/weight/order behavior. Update tests and verify the implementation.",
                    "",
                    "",
                ),
            ],
            "drift": {
                "wire.md": "Parcel envelope wire contract: envelope key is shipments, schema field is wire_rev with value 8. Each parcel row has identifier key parcel_ref and weight key grams.\n"
            },
        },
        {
            "id": "agenda",
            "topic": "agenda event",
            "docs": {
                "wire.md": "Agenda event wire contract: envelope key is entries, schema field is agenda_rev with value 3. Each event has event_ref and begins_utc fields.\n",
                "identity.md": "Agenda event identifiers use the input identifier stripped and lowercased, followed by @desk.local. Empty identifiers or internal whitespace raise ValueError. Duplicate resulting identifiers reject an agenda export with ValueError.\n",
                "time.md": "Agenda event local timestamps use the exact format YYYY-MM-DD HH:MM in a fixed UTC+08:00 offset. Serialize begins_utc as YYYYMMDDTHHMMSSZ. Invalid dates or formats raise ValueError.\n",
                "order.md": "Agenda event export excludes rows whose done is true. Remaining entries are sorted by begins_utc, then event_ref; do not mutate caller data. Empty exports still contain schema and empty entries.\n",
                "display.md": "Agenda event display labels use title case. A displayed time is local wall clock time, whereas persisted begins_utc uses the wire timestamp contract.\n",
            },
            "tasks": [
                module(
                    "agenda_export.py",
                    "Implement export(rows) for agenda event export following the repository's wire, identity, time and order contracts. Rows have id, local, and done keys; return a dictionary, not JSON text. Completed rows should be ignored before validation.",
                    """
                    from datetime import datetime, timedelta
                    import re
                    def export(rows):
                        out=[]; seen=set()
                        for r in rows:
                            if r['done']: continue
                            k=r['id'].strip().lower()
                            if not k or any(c.isspace() for c in k): raise ValueError('id')
                            k+='@desk.local'
                            if k in seen: raise ValueError('duplicate')
                            seen.add(k)
                            if not re.fullmatch(r'\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}',r['local']): raise ValueError('time')
                            t=datetime.strptime(r['local'],'%Y-%m-%d %H:%M')-timedelta(hours=8)
                            out.append({'event_ref':k,'begins_utc':t.strftime('%Y%m%dT%H%M%SZ')})
                        return {'agenda_rev':3,'entries':sorted(out,key=lambda x:(x['begins_utc'],x['event_ref']))}
                """,
                    r"""
                    from agenda_export import export
                    r=[{'id':' B ','local':'2026-10-01 00:10','done':False},{'id':'a','local':'2026-10-01 00:10','done':False},{'id':'','local':'bad','done':True}]
                    before=[dict(x) for x in r]
                    assert export(r)=={'agenda_rev':3,'entries':[{'event_ref':'a@desk.local','begins_utc':'20260930T161000Z'},{'event_ref':'b@desk.local','begins_utc':'20260930T161000Z'}]}
                    assert r==before and export([])=={'agenda_rev':3,'entries':[]}
                    for local in ['2026-02-30 00:00','2026-1-01 00:00','2026-01-01T00:00']:
                        with pytest.raises(ValueError): export([{'id':'x','local':local,'done':False}])
                    with pytest.raises(ValueError): export([{'id':'X','local':'2026-01-01 00:00','done':False},{'id':' x ','local':'2026-01-01 00:00','done':False}])
                """,
                ),
                module(
                    "agenda_reminders.py",
                    "Implement reminders(rows, minutes) for agenda event reminders. Follow repository identity/time conventions and exclude completed rows before validation. For each remaining row return {'event_ref': identifier, 'remind_utc': serialized UTC timestamp minutes earlier than local start}. Sort by remind_utc then event_ref. minutes must be a nonnegative integer (bool is invalid); raise ValueError otherwise. Duplicate identifiers reject. Do not mutate input.",
                    """
                    from agenda_export import export
                    from datetime import datetime, timedelta
                    def reminders(rows,minutes):
                        if type(minutes) is not int or minutes<0: raise ValueError('minutes')
                        entries=export(rows)['entries']
                        out=[{'event_ref':e['event_ref'],'remind_utc':(datetime.strptime(e['begins_utc'],'%Y%m%dT%H%M%SZ')-timedelta(minutes=minutes)).strftime('%Y%m%dT%H%M%SZ')} for e in entries]
                        return sorted(out,key=lambda e:(e['remind_utc'],e['event_ref']))
                """,
                    r"""
                    from agenda_reminders import reminders
                    assert reminders([{'id':' X ','local':'2026-10-01 00:10','done':False}],30)==[{'event_ref':'x@desk.local','remind_utc':'20260930T154000Z'}]
                    assert reminders([],0)==[]
                    for n in [-1,True,1.5]:
                        with pytest.raises(ValueError): reminders([],n)
                """,
                ),
                module(
                    "agenda_export.py",
                    "The agenda event wire contract has been revised in docs/wire.md. Update export(rows) to that contract while preserving time/identity/order behavior. Keep reminders(rows, minutes) working too; update tests and verify both public functions.",
                    "",
                    "",
                ),
            ],
            "drift": {
                "wire.md": "Agenda event wire contract: envelope key is events, schema field is agenda_rev with value 4. Each event has event_ref and begins_utc fields.\n"
            },
        },
    ]


def finish_projects():
    projects = memory_projects()
    for p in projects:
        before, after, old, new = (
            ("parcels", "shipments", 7, 8) if p["id"] == "parcel" else ("entries", "events", 3, 4)
        )
        for key in ("reference", "checks"):
            p["tasks"][2][key] = (
                p["tasks"][0][key]
                .replace(repr(before), repr(after))
                .replace(":" + str(old), ":" + str(new))
            )
        p["files"] = {
            "README.md": "Project-specific conventions are in docs/. These documents are authoritative for persisted data.\n",
            **{"docs/" + k: v for k, v in p["docs"].items()},
            **{m["path"]: "# Implementation pending.\n" for m in p["tasks"]},
        }
    return projects


def grader(modules):
    header = "import sys\nfrom pathlib import Path\nimport pytest\nsys.path.insert(0, str(Path(__file__).parent))\n"
    return header + "\n".join(
        "def test_contract_"
        + str(i)
        + "():\n"
        + "".join("    " + line + "\n" for line in m["checks"].splitlines())
        for i, m in enumerate(modules)
    )
