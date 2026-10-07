"""New probes over previously used source revisions; not an unseen-repository test."""
from copy import deepcopy

CASES = [
    ("django", [
        ("containers", "ds", [
            ("d1", "import copy; d=ds.MultiValueDict({'a':[1,2]}); a=copy.copy(d); b=copy.deepcopy(d); d.appendlist('a',3); result=[d.getlist('a'),a.getlist('a'),b.getlist('a')]", "MultiValueDict.__copy__"),
            ("d2", "d=ds.MultiValueDict({'k':[0]}); d.update([('k',1),('k',2)], extra=3); result=[d.getlist('k'),d.getlist('extra')]", "MultiValueDict.update"),
            ("d3", "x=ds.ImmutableList([1,2],warning='locked'); x.append(3); result=list(x)", "ImmutableList.complain"),
            ("d4", "s=ds.OrderedSet(['b','a','b']); s.add('c'); s.discard('missing'); s.remove('a'); result=[list(s),len(s),'a' in s]", "OrderedSet.__iter__"),
        ]),
        ("normalization", "rx", [
            ("r1", r"result=rx.normalize(r'^(?P<name>[a-z]+)/(?P=name)/$')", "normalize"),
            ("r2", r"result=rx.normalize(r'^file/[A-Z]{2}\.(?:json|txt)$')", "normalize"),
            ("r3", r"result=rx.normalize(r'^item/(?:page/)?(?P<pk>\d{2})/$')", "normalize"),
            ("r4", r"result=rx.normalize(r'^ab{0,2}c+$')", "normalize"),
        ]),
    ]),
    ("openlibrary", [
        ("seed lists", "engine", [
            ("e1", "result=engine.get_seeds({'key':'/works/X','authors':[{'author':{'key':'/authors/A'}},{'author':{'key':'/authors/A'}}],'editions':[{'key':'/books/B'},{'key':'/books/B'}],'subjects':['X Y','x_y'],'subject_people':['X Y']})", "get_seeds"),
            ("e2", "s=engine.SubjectProcessor(); s.add_subjects({'subjects':['A B','a_b','a_b'],'places':['A B'],'times':[17,'A B']}); result=s.top_subjects()", "SubjectProcessor.top_subjects"),
            ("e3", "result=engine.reduce_seeds([(0,2,0,'2024',{}),(1,0,1,'2023',{'times':['Now']}),(2,1,0,'2025',{'times':['now']})])", "reduce_seeds"),
            ("e4", "s=engine.SubjectProcessor(); s.add_subjects({'subjects':['B','A','B','C']}); result=[s.top_subjects(0),s.top_subjects(-1)]", "SubjectProcessor.top_subjects"),
        ]),
        ("compression inputs", "compress", [
            ("c1", r"c=compress.Compressor('catalog'); a=c.compress('目录\né'); b=c.compress('目录\né'.encode()); result=[a==b,c.decompress(b)]", "Compressor.compress"),
            ("c2", r"c=compress.Compressor('seed'); result=c.compress(b'\xff')", "Compressor.compress"),
            ("c3", "c=compress.Compressor(b'seed'); result=True", "Compressor.__init__"),
            ("c4", "c=compress.Compressor(''); blocks=[c.compress(s) for s in ['a','a','']]; result=[blocks[0]==blocks[1],[c.decompress(x) for x in reversed(blocks)]]", "Compressor.decompress"),
        ]),
    ]),
    ("session", [
        ("watermarks and removal", None, [
            ("v1", "s=events.EventSource(types.Clock()); s.register_source('a'); s.register_source('b'); s.ingest(types.Event(b'k',7,1,'a')); s.ingest(types.Event(b'k',4,1,'b')); s.ingest(types.Event(b'k',2,1,'a')); result=s.watermark", "events:EventSource.ingest"),
            ("v2", "s=events.EventSource(types.Clock()); s.ingest(types.Event(b'k',9,1,'a')); s.register_source('a'); s.advance_time(2); result=s.watermark", "events:EventSource.register_source"),
            ("s1", "s=sessions.SessionManager(types.Clock(),5,merger.Merger()); a=s.process_event(types.Event(b'a',10,2)); b=s.process_event(types.Event(b'b',10,3)); result=[len(s.all_sessions()),a is b,a.aggregate.result(),b.aggregate.result()]", "sessions:SessionManager.process_event"),
            ("s2", "s=sessions.SessionManager(types.Clock(),5,merger.Merger()); x=s.process_event(types.Event(b'k',10,1)); s.remove_session(x); s.remove_session(x); result=[len(s.all_sessions()),s.get_sessions(b'k')]", "sessions:SessionManager.remove_session"),
        ]),
        ("emission and collection", None, [
            ("f1", "x=types.Session(b'k',0,10,1); x.aggregate.add(-3); e=emitter.Emitter(5); first=e.evaluate(x,16); repeat=e.evaluate(x,17); result=[first.is_retraction,repeat is None,x.fire_count,first.value]", "emitter:Emitter.evaluate"),
            ("f2", "x=types.Session(b'k',0,10,1); x.aggregate.add(-3); e=emitter.Emitter(5); e.evaluate(x,16); x.aggregate.add(-8); y=e.evaluate(x,17); result=[y.is_retraction,y.retracted_value,y.value]", "emitter:Emitter.evaluate"),
            ("g1", "a=types.Session(b'a',0,10,1,fired=True); b=types.Session(b'b',0,10,1); g=gc.GarbageCollector(7,100,5); result=[[x.key.decode() for x in g.collect([a,b],w)] for w in [22,23]]", "gc:GarbageCollector.collect"),
            ("g2", "a=types.Session(b'a',0,100,1); b=types.Session(b'b',0,0,10); g=gc.GarbageCollector(7,20,5); result=[[x.key.decode() for x in g.collect([a,b],w)] for w in [21,22]]", "gc:GarbageCollector.collect"),
        ]),
    ]),
]


def bank(base):
    originals = {task["id"]: task for task in base.bank()}
    prior_codes = {q["code"] for task in originals.values() for group in task["groups"] for q in group["questions"]}
    tasks = []
    for project, groups in CASES:
        task = deepcopy(originals[project + "-investigate"])
        task.update(id=project + "-handoff", groups=[], oracle=[])
        for name, alias, questions in groups:
            task["groups"].append({"name": name, "questions": [{"id": key, "code": code} for key, code, _ in questions]})
            for key, code, symbol in questions:
                assert code not in prior_codes
                module, symbol = (alias, symbol) if alias else symbol.split(":", 1)
                task["oracle"].append({"id": key, "code": code, "evidence_path": task["aliases"][module], "evidence_symbol": symbol})
        tasks.append(task)
    return tasks
