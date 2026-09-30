"""Frozen supplementary diagnostics; never changes the original benchmark score.

Derived from worker-focused-20260929/diagnostics/public_examples.py.
The controller uses the standard library; candidate execution uses the existing
offline Python 3.9 environment in a read-only Landlock/seccomp sandbox.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PYTHON = Path('/tmp/aster-worker-repository-py39-20260928/bin/python')
LAUNCHER = REPO / 'src/aster/safety/linux.py'
TIMEOUT = 20
PRINTER_FILES = {
    'str': 'sympy/printing/tests/test_str.py',
    'pretty': 'sympy/printing/pretty/tests/test_pretty.py',
    'latex': 'sympy/printing/tests/test_latex.py',
}


def gamma_p(a, x):
    term = total = 1 / a
    for n in range(1, 10000):
        term *= x / (a + n)
        total += term
        if abs(term) < abs(total) * 1e-15:
            return math.exp(a * math.log(x) - x - math.lgamma(a)) * total
    raise ArithmeticError("reference series did not converge")


def student_cdf(x):
    n, h = 4096, abs(x) / 4096
    f = lambda t: math.gamma(5.5) / (math.sqrt(10 * math.pi) * math.gamma(5)) * (1 + t*t/10)**-5.5
    area = h / 3 * (f(0) + f(abs(x)) + sum((4 if i % 2 else 2) * f(i*h) for i in range(1, n)))
    return 0.5 + math.copysign(area, x)


# Constructors and first evaluation points are verbatim from the public issue.
CDF = [
    ('Arcsin', 'Arcsin("x", 0, 3)', ['1', 'S(1)/2', '2'], '[0,3]', lambda x: 2/math.pi*math.asin(math.sqrt(x/3))),
    ('Dagum', 'Dagum("x", S(1)/3, S(1)/5, 2)', ['3', '1', '4'], '(0,infinity)', lambda x: (1+(x/2)**(-1/5))**(-1/3)),
    ('Erlang', 'Erlang("x", 1, 1)', ['1', 'S(1)/2', '2'], '[0,infinity)', lambda x: 1-math.exp(-x)),
    ('Frechet', 'Frechet("x", S(4)/3, 1, 2)', ['3', 'S(5)/2', '4'], '(2,infinity)', lambda x: math.exp(-(x-2)**(-4/3))),
    ('Gamma', 'Gamma("x", 0.1, 2)', ['3', '1', '5'], '[0,infinity)', lambda x: gamma_p(.1,x/2)),
    ('GammaInverse', 'GammaInverse("x", S(5)/7, 2)', ['3', '1', '5'], '(0,infinity)', lambda x: 1-gamma_p(5/7,2/x)),
    ('Kumaraswamy', 'Kumaraswamy("x", S(1)/123, 5)', ['S(1)/3', 'S(1)/10**123', 'S(1)/2**123'], '[0,1]', lambda x: 1-(1-x**(1/123))**5),
    ('Laplace', 'Laplace("x", 2, 3)', ['5', '-1', '2'], 'real', lambda x: .5*math.exp((x-2)/3) if x<2 else 1-.5*math.exp(-(x-2)/3)),
    ('Logistic', 'Logistic("x", 1, 0.1)', ['2', '0', '1'], 'real', lambda x: 1/(1+math.exp(-(x-1)/.1))),
    ('Nakagami', 'Nakagami("x", S(7)/3, 1)', ['2', 'S(1)/2', '1'], '[0,infinity)', lambda x: gamma_p(7/3,7*x*x/3)),
    ('StudentT', 'StudentT("x", 10)', ['2', '-2', '0'], 'real', student_cdf),
    ('UniformSum', 'UniformSum("x", 5)', ['2', '1', '4'], '[0,5]', lambda x: sum((-1)**k*math.comb(5,k)*(x-k)**5 for k in range(math.floor(x)+1))/math.factorial(5)),
]
# Finite support boundaries and immediately outside support are explicit cases.
# +/-oo on real support checks the API's endpoint substitution, not a limit proof.
BOUNDARIES = {
    'Arcsin': [('-1', 0), ('0', 0), ('3', 1), ('4', 1)],
    'Frechet': [('1', 0), ('2', 0), ('oo', 1)],
    'Kumaraswamy': [('-1', 0), ('0', 0), ('1', 1), ('2', 1)],
    'UniformSum': [('-1', 0), ('0', 0), ('5', 1), ('6', 1)],
    **{name: [('-oo', 0), ('oo', 1)] for name in ('Laplace', 'Logistic', 'StudentT')},
    **{name: [('-1', 0), ('0', 0), ('oo', 1)] for name in
       ('Dagum', 'Erlang', 'Gamma', 'GammaInverse', 'Nakagami')},
}
CHILD = r'''
import json,math,sys
from pathlib import Path
import sympy
from sympy import S, Float, Integral, Sum, MatrixSymbol, latex, pretty, oo
from sympy.stats import *
case=json.loads(sys.argv[1]); workspace=Path(sys.argv[2]).resolve()
if not Path(sympy.__file__).resolve().is_relative_to(workspace):
    raise RuntimeError("candidate SymPy source was not loaded")
if case["kind"]=="printer":
    A,B=MatrixSymbol("A",2,2),MatrixSymbol("B",2,2)
    expression=A-A*B-B
    print(json.dumps({"observed":str(expression) if case["name"]=="str" else pretty(expression,use_unicode=True) if case["name"]=="pretty" else latex(expression)}),flush=True)
else:
    observation={"point":case["point"]}
    try:
        value=cdf(eval(case["constructor"]))(eval(case["point"]))
        observation.update(expression=str(value),float_atom=value.has(Float),
            unevaluated_integral=value.has(Integral),unevaluated_sum=value.has(Sum))
        if not value.has(Integral):
            numeric=float(value.evalf(18))
            observation["numeric"]=numeric if math.isfinite(numeric) else None
            observation["nonfinite"]=not math.isfinite(numeric)
    except Exception as exc:
        observation.update(error=type(exc).__name__,detail=str(exc)[:500])
    print(json.dumps(observation),flush=True)
'''


def cases(kind):
    if kind=='printing':
        return [{'kind':'printer','name':name,'requirement':'A - A*B - B renders as signed subtraction'} for name in ('str','pretty','latex')]
    result=[]
    for name,expr,points,support,oracle in CDF:
        samples=[('interior',p,oracle(float(eval(p, {'__builtins__':{},'S':float})))) for p in points]
        samples += [('boundary',p,expected) for p,expected in BOUNDARIES[name]]
        result.extend(dict(kind='cdf',name=name,constructor=expr,point=p,group=group,
            support=support,expected=expected,expected_exact=name=='Erlang')
            for group,p,expected in samples)
    return result


def classify(case, observations, timed_out, exit_code):
    if timed_out or exit_code or not observations:
        return 'unknown', 'timeout, process/import failure or absent observations; inspect logs'
    if case['kind']=='printer':
        text=observations[0].get('observed',''); compact=''.join(text.split()).replace('⋅','*').replace(r'\cdot','*')
        if case['name']=='latex':
            compact=compact.replace('AB','A*B')
        terms=[(1,'A'),(-1,'A*B'),(-1,'B')]
        accepted={''.join(('+' if sign>0 else '-')+term for sign,term in order).lstrip('+') for order in itertools.permutations(terms)}
        if compact in accepted:
            return 'verified','standard signed terms for the public expression; order/spacing allowed'
        if re.fullmatch(r'[AB1()*+\-]+',compact):
            if '1' in compact or '+-' in compact:
                return 'wrong','explicit unit coefficient or plus-negative form violates issue formatting'
            if re.fullmatch(r'[+\-]?(?:A\*B|A|B)(?:[+\-](?:A\*B|A|B))*',compact):
                return 'wrong','recognized signed terms have a missing/extra term or wrong sign'
        return 'unknown','unsupported rendering; needs manual equivalence and formatting inspection'
    observation=observations[0]
    if observation.get('unevaluated_integral'):
        return 'wrong','public CDF remains an unevaluated Integral'
    if case['expected_exact'] and observation.get('float_atom'):
        return 'wrong','exact Erlang input produced a Float atom'
    if observation.get('nonfinite'):
        return 'wrong','candidate returned a nonfinite probability'
    if 'error' in observation or 'numeric' not in observation:
        return 'unknown','candidate exception or incomplete observation; not automatically an omission'
    value=observation['numeric']
    if not math.isfinite(value) or not 0<=value<=1 or not math.isclose(value,case['expected'],rel_tol=1e-8,abs_tol=1e-10):
        return 'wrong','nonfinite/out-of-range probability or mismatch with independent reference'
    return 'verified','fixed numeric point only; no full symbolic/parameter correctness claim'


def file_qualified_tests(log):
    """Preserve native SymPy file headers; never merge same-named tests."""
    records={}; current=None
    statuses={'ok':'passed','F':'failed','E':'error','f':'xfail','X':'xpass','s':'skipped'}
    for line in log.splitlines():
        header=re.match(r'^(sympy/\S+\.py)\[\d+\]',line.strip())
        if header:
            current=header[1]
        match=re.match(r'^(test_\S+) (ok|F|E|f|X|s)(?:\s|$)',line.strip())
        if current and match:
            key=current+'::'+match[1]
            records.setdefault(key,[]).append(statuses[match[2]])
    return {key:dict(status=values[0] if len(set(values))==1 else 'unknown',observations=values)
            for key,values in records.items()}


def summarize(records):
    names=list(dict.fromkeys(row['case']['name'] for row in records))
    result={}
    for name in names:
        rows=[row for row in records if row['case']['name']==name]
        groups={row['case'].get('group','public_expression') for row in rows}
        result[name]={}
        for group in sorted(groups|{'all'}):
            selected=[row for row in rows if group=='all' or row['case'].get('group','public_expression')==group]
            counts={label:sum(row['status']==label for row in selected) for label in ('verified','wrong','unknown')}
            result[name][group]=dict(counts=counts,status='wrong' if counts['wrong'] else 'unknown' if counts['unknown'] else 'verified')
    return result


def run(workspace, task, python=PYTHON, launcher=LAUNCHER, selected=None, grading_log=None):
    workspace=Path(workspace).resolve(strict=True)
    records=[]
    for case in cases(task) if selected is None else selected:
        with tempfile.TemporaryDirectory(prefix='aster-public-diagnostic-') as tmp:
            command=shlex.join([str(python),'-B','-c',CHILD,json.dumps(case),str(workspace)])
            env={'PATH':str(python.parent)+':/usr/bin:/bin','HOME':tmp,'TMPDIR':tmp,'PYTHONPATH':str(workspace),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
            started=time.monotonic(); timed_out=False
            with open(Path(tmp)/'stdout','w+') as out, open(Path(tmp)/'stderr','w+') as err:
                proc=subprocess.Popen([str(python),'-B',str(launcher),tmp,'off',command,str(workspace)],env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True)
                try: proc.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    timed_out=True
                    try: os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait()
                out.seek(0); stdout=out.read(32768); err.seek(0); stderr=err.read(32768)
            observations=[]
            for line in stdout.splitlines():
                try: observations.append(json.loads(line))
                except json.JSONDecodeError: pass
            label,reason=classify(case,observations,timed_out,proc.returncode)
            records.append(dict(case=case,status=label,reason=reason,observations=observations,seconds=time.monotonic()-started,timed_out=timed_out,exit_code=proc.returncode,stdout=stdout,stderr=stderr))
    report=dict(workspace=str(workspace),task=task,scope='supplementary fixed public points and boundaries; original score unchanged',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),launcher_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
        python=str(python),timeout_seconds_per_point=TIMEOUT,results=records,summary=summarize(records))
    if task=='printing':
        tests=file_qualified_tests(grading_log.read_text()) if grading_log else {}
        report['upstream_tests']={file+'::test_MatrixSymbol_printing':tests.get(file+'::test_MatrixSymbol_printing',dict(status='unknown',observations=[])) for file in PRINTER_FILES.values()}
        report['all_file_qualified_tests']=tests
        report['grading_log_sha256']=hashlib.sha256(grading_log.read_bytes()).hexdigest() if grading_log else None
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',type=Path,required=True); p.add_argument('--task',choices=('printing','cdf'),required=True)
    p.add_argument('--python',type=Path,default=PYTHON); p.add_argument('--launcher',type=Path,default=LAUNCHER)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--grading-log',type=Path)
    args=p.parse_args()
    if args.output.resolve().is_relative_to(args.workspace.resolve()): raise ValueError('output must stay outside candidate')
    report=run(args.workspace,args.task,args.python,args.launcher,grading_log=args.grading_log)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
