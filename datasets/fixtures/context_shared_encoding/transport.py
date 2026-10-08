import json


def request_body(payload):
    return json.dumps(payload).encode("utf-8")
