import json


def cache_value(payload):
    return json.dumps(payload).encode("utf-8")
