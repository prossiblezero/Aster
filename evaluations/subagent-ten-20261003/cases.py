"""Six unchanged historical tasks plus four independently sourced investigations."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPSTREAM = REPO / "artifacts/private/subagent-ten-20261003/upstream"

# Each tuple is (question id, executable probe, module alias, evidence symbol).
# Answers are computed outside the agent's sandbox, never included in REQUEST.md.
ADDITIONS = {
    "boltons": (
        {"d": "boltons/dictutils.py", "i": "boltons/iterutils.py"},
        [
            ("ordered multimaps", [
                ("d1", "x=d.OrderedMultiDict([('a',1),('b',2),('a',3)]); result=[list(x.items()),list(x.items(multi=True)),x.getlist('a')]", "d", "OrderedMultiDict.iteritems"),
                ("d2", "x=d.OrderedMultiDict([('a',1),('a',2),('b',3)]); x.update({'a':9}); result=[x.getlist('a'),list(x.items(multi=True))]", "d", "OrderedMultiDict.update"),
                ("d3", "x=d.OrderedMultiDict([('a',1),('b',2),('a',3)]); result=[x.poplast('a'),list(x.items(multi=True)),x.popall('a'),list(x.items(multi=True))]", "d", "OrderedMultiDict.poplast"),
                ("d4", "x=d.OneToOne({'a':1,'b':2}); x['c']=1; result=[dict(x),dict(x.inv)]", "d", "OneToOne.__setitem__"),
            ]),
            ("iteration boundaries", [
                ("i1", "result=[i.chunked(range(5),2),i.chunked(range(5),2,fill=None)]", "i", "chunked_iter"),
                ("i2", "result=[i.split([None,1,None,None,2,None]),i.split([None,1,None,None,2,None],sep=[None])]", "i", "split_iter"),
                ("i3", "result=[i.windowed([1,2],3),i.windowed([1,2],3,fillvalue=0)]", "i", "windowed_iter"),
                ("i4", "result=i.unique(['A','b','a','B','c'],key=str.lower)", "i", "unique_iter"),
            ]),
        ],
    ),
    "more-itertools": (
        {"m": "more_itertools/more.py", "r": "more_itertools/recipes.py"},
        [
            ("buffering and boundaries", [
                ("m1", "it=iter(range(5)); chunks=m.chunked(it,2,strict=True); first=next(chunks); second=next(chunks); result=[first,second,next(chunks)]", "m", "chunked"),
                ("m2", "result=[list(m.windowed([1,2],3,fillvalue=0)),list(m.windowed(range(5),3,step=3,fillvalue=-1))]", "m", "windowed"),
                ("m3", "head,tail=m.spy(iter([1,2,3]),2); result=[head,list(tail)]", "m", "spy"),
                ("m4", "result=[list(m.split_at([0,1,0,0,2,0],lambda x:x==0)),list(m.split_at([0,1,0,0,2,0],lambda x:x==0,maxsplit=2,keep_separator=True))]", "m", "split_at"),
            ]),
            ("iterator recipes", [
                ("r1", "false,true=r.partition(lambda x:x%2,[0,1,2,3,4]); result=[list(true),list(false)]", "r", "partition"),
                ("r2", "result=list(r.unique_everseen([[1],[2],[1],[3],[2]]))", "r", "unique_everseen"),
                ("r3", "result=list(r.grouper('ABCDEFG',3,incomplete='ignore'))", "r", "grouper"),
                ("r4", "result=[list(r.roundrobin('AB','C','DEF')),list(r.powerset([1,1]))]", "r", "roundrobin"),
            ]),
        ],
    ),
    "cachetools": (
        {"c": "cachetools/__init__.py", "k": "cachetools/keys.py"},
        [
            ("eviction and accounting", [
                ("c1", "x=c.LRUCache(2); x['a']=1; x['b']=2; ignored=x['a']; x['c']=3; result=[sorted(x),x.currsize]", "c", "LRUCache.__getitem__"),
                ("c2", "x=c.FIFOCache(2); x['a']=1; x['b']=2; x['a']=9; x['c']=3; result=[sorted(x.items()),x.currsize]", "c", "FIFOCache.__setitem__"),
                ("c3", "x=c.Cache(5,getsizeof=len); x['a']=[1,2]; x['a'].append(3); x['b']=[4,5,6]; result=[sorted(x.items()),x.currsize]", "c", "Cache.__setitem__"),
                ("c4", "now=[0]; x=c.TTLCache(2,5,timer=lambda:now[0]); x['a']=1; now[0]=5; result=['a' in x,list(x.expire()),len(x)]", "c", "TTLCache.expire"),
            ]),
            ("cache key equivalence", [
                ("k1", "result=[k.hashkey(1)==k.hashkey(1.0),k.typedkey(1)==k.typedkey(1.0)]", "k", "typedkey"),
                ("k2", "result=[k.hashkey(a=1,b=2)==k.hashkey(b=2,a=1),k.hashkey(1)==k.hashkey(a=1)]", "k", "hashkey"),
                ("k3", "result=[k.methodkey(object(),1,x=2)==k.methodkey(object(),1,x=2),k.typedmethodkey(object(),1)==k.typedmethodkey(object(),1.0)]", "k", "typedmethodkey"),
                ("k4", "x=k.hashkey([1]); result=hash(x)", "k", "_HashedTuple.__hash__"),
            ]),
        ],
    ),
    "itsdangerous": (
        {"s": "itsdangerous/signer.py", "j": "itsdangerous/serializer.py", "u": "itsdangerous/url_safe.py"},
        [
            ("signing and key rotation", [
                ("s1", "old=s.Signer('old'); rotating=s.Signer(['old','new']); token=old.sign('payload'); result=[rotating.unsign(token).decode(),old.validate(rotating.sign('payload')),rotating.secret_key.decode()]", "s", "Signer.verify_signature"),
                ("s2", "x=s.Signer('secret',sep='-'); result=x.sign('x').decode()", "s", "Signer.__init__"),
                ("s3", "a=s.Signer('secret',salt='a'); b=s.Signer('secret',salt='b'); result=[a.validate(a.sign('x.y')),b.validate(a.sign('x.y')),a.unsign(a.sign('x.y')).decode()]", "s", "Signer.unsign"),
                ("s4", "x=s.Signer('secret',algorithm=s.NoneAlgorithm()); token=x.sign('payload'); result=[token.decode(),x.validate(token),x.validate(b'changed.')]", "s", "NoneAlgorithm.get_signature"),
            ]),
            ("serialization and untrusted payloads", [
                ("j1", "a=j.Serializer('a'); b=j.Serializer('b'); token=a.dumps({'x':[1,2]}); result=[a.loads_unsafe(token),b.loads_unsafe(token)]", "j", "Serializer._loads_unsafe_impl"),
                ("j2", "x=j.Serializer('secret',salt='default'); token=x.dumps([1,2],salt='special'); result=x.loads(token)", "j", "Serializer.loads"),
                ("u1", "x=u.URLSafeSerializer('secret'); token=x.dumps('a'*1000); result=[token.startswith('.'),x.loads(token)==('a'*1000),isinstance(token,str)]", "u", "URLSafeSerializerMixin.dump_payload"),
                ("u2", "x=u.URLSafeSerializer('secret'); result=x.loads_unsafe('not-signed')", "j", "Serializer._loads_unsafe_impl"),
            ]),
        ],
    ),
}


def bank(base):
    tasks = base.bank()
    for project, (aliases, groups) in ADDITIONS.items():
        metadata = json.loads((UPSTREAM / (project + ".json")).read_text())
        tasks.append(dict(
            id=project + "-investigate", kind="investigate", project=project,
            aliases=aliases, upstream=metadata,
            groups=[dict(name=name, questions=[dict(id=q[0], code=q[1]) for q in questions])
                    for name, questions in groups],
            oracle=[dict(id=q[0], code=q[1], evidence_path=aliases[q[2]], evidence_symbol=q[3])
                    for _, questions in groups for q in questions],
        ))
    assert len(tasks) == len({t["id"] for t in tasks}) == 10
    return tasks


def prepare(base, task, root):
    if "upstream" not in task:
        return base.prepare(task, root)
    root.mkdir(parents=True, exist_ok=False)
    for item in task["upstream"]["files"]:
        content = (UPSTREAM / task["project"] / item["path"]).read_bytes()
        assert base.sha(content) == item["sha256"]
        target = root / item["path"].removeprefix("src/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (root / "REQUEST.md").write_text(base.prompt(task))


PROBE = """import importlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));data=json.load(sys.stdin)
modules={}
for alias,path in data['aliases'].items():
 name=path.removesuffix('.py').replace('/','.').removesuffix('.__init__')
 module=importlib.import_module(name)
 assert Path(module.__file__).resolve()==(root/path).resolve()
 modules[alias]=module
try:
 exec(data['code'],modules)
 value=modules['result']
except Exception as exc:
 value={'exception':type(exc).__name__}
print(json.dumps(value,ensure_ascii=False))
"""


def oracle(base, task, root, ranges):
    if "upstream" not in task:
        rows = base.oracle(task, root)
        for row in rows:
            row["evidence_ranges"] = ranges((root / row["evidence_path"]).read_text())
        return rows
    rows = []
    for query in task["oracle"]:
        run = subprocess.run([sys.executable, "-B", "-c", PROBE, str(root)],
                             input=json.dumps(dict(aliases=task["aliases"], code=query["code"])),
                             text=True, capture_output=True, timeout=20, check=True)
        index = ranges((root / query["evidence_path"]).read_text())
        rows.append(query | dict(observed=json.loads(run.stdout),
                                 lines=index[query["evidence_symbol"]], evidence_ranges=index))
    return rows
