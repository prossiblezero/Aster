"""Supplementary public-example diagnostics; never changes a frozen benchmark score."""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time


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
CHILD = r'''
import json,sys
from pathlib import Path
import sympy
from sympy import S, Float, Integral, MatrixSymbol, latex, pretty
from sympy.stats import *
case=json.loads(sys.argv[1]); workspace=Path(sys.argv[2]).resolve()
if not Path(sympy.__file__).resolve().is_relative_to(workspace):
    raise RuntimeError("candidate SymPy source was not loaded")
if case["kind"]=="printer":
    A,B=MatrixSymbol("A",2,2),MatrixSymbol("B",2,2)
    expression=A-A*B-B
    print(json.dumps({"observed":str(expression) if case["name"]=="str" else pretty(expression,use_unicode=True) if case["name"]=="pretty" else latex(expression)}),flush=True)
else:
    for point in case["points"]:
        try:
            value=cdf(eval(case["constructor"]))(eval(point))
            unevaluated=value.has(Integral)
            print(json.dumps({"point":point,"expression":str(value),"float_atom":value.has(Float),"unevaluated_integral":unevaluated,"numeric":None if unevaluated else float(value.evalf(18))}),flush=True)
        except Exception as exc:
            print(json.dumps({"point":point,"error":type(exc).__name__,"detail":str(exc)[:500]}),flush=True)
'''


def cases(kind):
    if kind=='printing':
        return [{'kind':'printer','name':name,'requirement':'A - A*B - B renders as signed subtraction'} for name in ('str','pretty','latex')]
    return [dict(kind='cdf',name=name,constructor=expr,points=points,support=support,
        expected=[oracle(float(eval(p, {'__builtins__':{},'S':float}))) for p in points],
        expected_exact=name=='Erlang') for name,expr,points,support,oracle in CDF]


def classify(case, observations, timed_out, exit_code):
    if timed_out or exit_code or not observations:
        return 'unknown', 'timeout, process/import failure or absent observations; inspect logs'
    if case['kind']=='printer':
        text=observations[0].get('observed',''); compact=''.join(text.split()).replace('⋅','*').replace(r'\cdot','*')
        terms=[(1,'A'),(-1,'AB' if case['name']=='latex' else 'A*B'),(-1,'B')]
        accepted={''.join(('+' if sign>0 else '-')+term for sign,term in order).lstrip('+') for order in itertools.permutations(terms)}
        return ('verified_public_example','one original expression, allowing term reordering') if compact in accepted else ('unknown','rendering needs manual equivalence inspection')
    if len(observations)!=3 or any('error' in o for o in observations):
        return 'unknown','candidate exception or incomplete observations; not automatically an omission'
    valid=all(not o['unevaluated_integral'] and o['numeric'] is not None and math.isfinite(o['numeric']) and 0<=o['numeric']<=1 and math.isclose(o['numeric'],expected,rel_tol=1e-8,abs_tol=1e-10) and (not case['expected_exact'] or not o['float_atom']) for o,expected in zip(observations,case['expected']))
    return ('verified_sample_points','three interior values only; not exhaustive symbolic/support proof') if valid else ('observed_mismatch','numeric, unevaluated-integral or Erlang exactness check differs; audit cause')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',type=Path,required=True); p.add_argument('--task',choices=('printing','cdf'),required=True)
    p.add_argument('--python',type=Path,default=Path('/tmp/aster-worker-repository-py39-20260928/bin/python'))
    p.add_argument('--launcher',type=Path,default=Path('/home/dzh/Aster/src/aster/safety/linux.py'))
    p.add_argument('--output',type=Path,required=True); p.add_argument('--timeout',type=float,default=20)
    args=p.parse_args(); workspace=args.workspace.resolve(strict=True)
    if args.timeout<=0: raise ValueError('timeout must be positive')
    if args.output.resolve().is_relative_to(workspace): raise ValueError('output must stay outside the candidate workspace')
    records=[]
    for case in cases(args.task):
        with tempfile.TemporaryDirectory(prefix='aster-public-diagnostic-') as tmp:
            command=shlex.join([str(args.python),'-B','-c',CHILD,json.dumps(case),str(workspace)])
            env={'PATH':str(args.python.parent)+':/usr/bin:/bin','HOME':tmp,'TMPDIR':tmp,'PYTHONPATH':str(workspace),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
            started=time.monotonic(); timed_out=False
            with open(Path(tmp)/'stdout','w+') as out, open(Path(tmp)/'stderr','w+') as err:
                proc=subprocess.Popen([str(args.python),'-B',str(args.launcher),tmp,'off',command,str(workspace)],env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True)
                try: proc.wait(timeout=args.timeout)
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
    report=dict(workspace=str(workspace),task=args.task,scope='supplementary public examples; not original grader or full requirement pass',script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),launcher_sha256=hashlib.sha256(args.launcher.read_bytes()).hexdigest(),python=str(args.python),results=records)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
