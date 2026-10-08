from pathlib import Path
import yaml

def test_fix():
    for service in ('auth','api','worker','gateway'):
        assert yaml.safe_load(open(f'services/{service}/config.yaml'))['schema_version'] == 2

def test_regression():
    for service,timeout in [('auth',20),('api',30),('worker',45),('gateway',25)]:
        data=yaml.safe_load(open(f'services/{service}/config.yaml'))
        assert data['service'] == service
        assert data['timeout_seconds'] == timeout
