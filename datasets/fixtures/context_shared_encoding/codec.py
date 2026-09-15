import json


def encode(payload):
    return json.dumps(payload).encode("utf-8")
