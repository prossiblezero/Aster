import ast
from pathlib import Path

def test_fix():
    assert Path('requirements.txt').read_text().strip() == 'requests==2.31'
    client=Path('src/client.py').read_text(); api=Path('src/api.py').read_text(); fetcher=Path('src/fetcher.py').read_text()
    assert 'trust_env = False' in client
    assert 'verify=True' in api and 'timeout=30' in api
    assert 'timeout=30' in fetcher

def test_regression():
    for path in Path('src').glob('*.py'):
        ast.parse(path.read_text())
