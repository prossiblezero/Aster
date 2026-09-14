from app import app

def test_fix():
    response=app.test_client().get('/health')
    assert response.status_code == 200
    assert 'def test_health' in open('tests/test_app.py').read()

def test_regression():
    assert app.test_client().get('/').status_code == 200
