from solution import merge_config


def test_scalar_override():
    assert merge_config({"port": 80}, {"port": 443}) == {"port": 443}
