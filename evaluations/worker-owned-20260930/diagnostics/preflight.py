"""No-model calibration on frozen upstream starter/reference and known defects."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

import public_examples as diagnostic

REPO = diagnostic.REPO
OUT = REPO / 'artifacts/private/worker-owned-20260930/diagnostic-preflight'
RECORD = REPO / 'artifacts/private/worker-redesign-20260928/objects/1c65a1678cd0a65fecf1f8d559e109debf0f9eed0423acbb79bb0f964ea84b0d'
BASE = REPO / 'artifacts/private/worker-redesign-20260928/objects/7eff2c58e95b5580c6ae8c4c1fbd0f6dd59cc65888b205a1e0e8f77d970d71b2'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def self_check():
    printer = diagnostic.cases('printing')[0]
    classify = lambda case, value: diagnostic.classify(case, [value], False, 0)[0]
    assert classify(printer, {'observed': 'A - A*B - B'}) == 'verified'
    assert classify(printer, {'observed': 'A + A*B - B'}) == 'wrong'
    assert classify(printer, {'observed': '(-1)*B + (-1)*A*B + A'}) == 'wrong'
    assert classify(printer, {'observed': '-B + -A*B + A'}) == 'wrong'
    assert classify(printer, {'observed': r'\text{unsupported}'}) == 'unknown'
    cdf = diagnostic.cases('cdf')[0]
    assert classify(cdf, {'numeric': cdf['expected']}) == 'verified'
    assert classify(cdf, {'numeric': 0.9}) == 'wrong'
    assert classify(cdf, {'unevaluated_integral': True}) == 'wrong'
    assert classify(cdf, {'error': 'TypeError'}) == 'unknown'
    assert diagnostic.classify(cdf, [], True, -9)[0] == 'unknown'
    erlang = next(case for case in diagnostic.cases('cdf') if case['name'] == 'Erlang')
    assert classify(erlang, {'numeric': erlang['expected'], 'float_atom': True}) == 'wrong'
    boundary = next(case for case in diagnostic.cases('cdf') if case['name'] == 'Arcsin' and case['point'] == '-1')
    assert classify(boundary, {'numeric': 1}) == 'wrong'
    parsed = diagnostic.file_qualified_tests('sympy/printing/tests/test_str.py[2]\ntest_same ok\n'
        'sympy/printing/tests/test_latex.py[2]\ntest_same F [FAIL]\n')
    assert len(parsed) == 2
    assert parsed['sympy/printing/tests/test_str.py::test_same']['status'] == 'passed'
    assert parsed['sympy/printing/tests/test_latex.py::test_same']['status'] == 'failed'
    return 'passed: signs, unit factors, unsupported format, unresolved integral, precision, boundary, unknown and file qualification'


MPMATH_REFERENCE = r'''
import json,sys,mpmath as mp
mp.mp.dps=80
def oracle(name,x):
    if name=='Arcsin': return mp.betainc(mp.mpf('.5'),mp.mpf('.5'),0,x/3,regularized=True)
    if name=='Dagum': return (1+(x/2)**(-mp.mpf(1)/5))**(-mp.mpf(1)/3)
    if name=='Erlang': return -mp.expm1(-x)
    if name=='Frechet': return mp.exp(-(x-2)**(-mp.mpf(4)/3))
    if name=='Gamma': return mp.gammainc(mp.mpf('.1'),0,x/2,regularized=True)
    if name=='GammaInverse': return mp.gammainc(mp.mpf(5)/7,2/x,mp.inf,regularized=True)
    if name=='Kumaraswamy': return -mp.expm1(5*mp.log1p(-x**(mp.mpf(1)/123)))
    if name=='Laplace': return mp.exp((x-2)/3)/2 if x<2 else 1-mp.exp(-(x-2)/3)/2
    if name=='Logistic': return 1/(1+mp.exp(-(x-1)/mp.mpf('.1')))
    if name=='Nakagami': return mp.gammainc(mp.mpf(7)/3,0,7*x*x/3,regularized=True)
    if name=='StudentT':
        tail=mp.betainc(5,mp.mpf('.5'),0,10/(10+x*x),regularized=True)/2
        return tail if x<0 else 1-tail
    if name=='UniformSum': return sum((-1)**k*mp.binomial(5,k)*(x-k)**5 for k in range(int(mp.floor(x))+1))/120
result=[]
for case in json.loads(sys.argv[1]):
    x=mp.mpf(eval(case['point'],{'__builtins__':{},'S':mp.mpf}))
    result.append(dict(name=case['name'],point=case['point'],expected=float(oracle(case['name'],x))))
print(json.dumps(dict(version=mp.__version__,module=mp.__file__,results=result)))
'''


def reference_check():
    import math
    cases = [case for case in diagnostic.cases('cdf') if case['group'] == 'interior']
    result = json.loads(subprocess.check_output([str(diagnostic.PYTHON), '-I', '-c', MPMATH_REFERENCE, json.dumps(cases)], text=True))
    for case, observation in zip(cases, result['results']):
        assert math.isclose(case['expected'], observation['expected'], rel_tol=1e-10, abs_tol=1e-12), (case, observation)
    result['status'] = 'passed'
    result['scope'] = '36 math references cross-checked against installed mpmath 80-digit formulas; no candidate SymPy import'
    return result


def save(name, report):
    path = OUT / (name + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(name, json.dumps(report.get('summary', report.get('status')), ensure_ascii=False), flush=True)
    return {'path': str(path.relative_to(REPO)), 'sha256': sha(path)}


def main():
    if (OUT / 'preflight.json').exists():
        raise FileExistsError('completed preflight preserved; use batch.py audit after freezing')
    OUT.mkdir(parents=True, exist_ok=True)
    assert sha(RECORD) == RECORD.name and sha(BASE) == BASE.name
    row = json.loads(RECORD.read_text())
    summary = {'self_check': self_check(), 'math_reference': save('math-reference', reference_check()), 'runs': {}}
    printing = REPO / 'artifacts/private/worker-code-20260928/repository-preflight/swe-verified-sympy__sympy-14248'
    old_grades = REPO / 'artifacts/private/worker-code-20260928/audit/repository-grader-v1/swe-verified-sympy__sympy-14248'
    for variant in ('starter', 'reference'):
        report = diagnostic.run(printing / (variant + '-workspace'), 'printing', grading_log=old_grades / variant / 'tests.log')
        assert all(part['all']['status'] == ('wrong' if variant == 'starter' else 'verified') for part in report['summary'].values())
        summary['runs']['printing-' + variant] = save('printing-' + variant, report)
    # Reconstruct CDF candidates only in the new private directory; old gates stay untouched.
    for variant in ('starter', 'reference'):
        workspace = OUT / ('cdf-' + variant + '-workspace')
        if workspace.exists():
            raise FileExistsError('preflight output already exists; preserve it rather than silently reusing')
        workspace.mkdir()
        with tarfile.open(BASE) as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if len(parts) > 1:
                    member.name = str(Path(*parts[1:]))
                    archive.extract(member, workspace, filter='data')
        if variant == 'reference':
            subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
            subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=row['patch'], text=True, cwd=workspace, check=True)
        selected = None if variant == 'reference' else [case for case in diagnostic.cases('cdf')
            if case['name'] in ('Arcsin', 'Erlang') and case['group'] == 'interior']
        report = diagnostic.run(workspace, 'cdf', selected=selected)
        report['source_crv_types_sha256'] = sha(workspace / 'sympy/stats/crv_types.py')
        report['calibration_subset'] = variant == 'starter'
        summary['runs']['cdf-' + variant] = save('cdf-' + variant, report)
    # Known defects from prior runs validate boundary/sign sensitivity, without editing them.
    old = REPO / 'artifacts/private/worker-focused-20260929'
    for repeat, name, point in ((3, 'Arcsin', '-1'), (1, 'StudentT', '-2')):
        workspace = old / f'repeat-{repeat:02d}/workspaces/holdout-sympy__sympy-13878--solo'
        selected = [case for case in diagnostic.cases('cdf') if case['name'] == name and case['point'] == point]
        report = diagnostic.run(workspace, 'cdf', selected=selected)
        assert report['results'][0]['status'] == 'wrong', report
        summary['runs']['known-' + name] = save('known-' + name, report)
    summary['status'] = 'calibrated; upstream reference need not satisfy stronger boundary diagnostics'
    summary['input_hashes'] = {str(path.relative_to(REPO)): sha(path) for path in
        (RECORD, BASE, diagnostic.LAUNCHER, diagnostic.HERE / 'public_examples.py', Path(__file__))}
    save('preflight', summary)


if __name__ == '__main__':
    main()
