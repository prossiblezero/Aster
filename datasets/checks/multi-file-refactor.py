from pathlib import Path
import importlib

def test_fix():
    mod=importlib.import_module('src.profile')
    assert callable(getattr(mod,'get_user_name',None))
    assert all('getUserName' not in p.read_text() for p in Path('src').glob('*.py'))
    assert mod.get_user_name({'name':' ada '}) == 'Ada'

def test_regression():
    from src.main import greeting, report
    assert greeting({'name':'grace'}) == 'Hello, Grace'
    assert report({'name':'alan'}) == 'User: Alan'
