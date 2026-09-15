from settings import load_settings


def request_timeout(raw):
    return load_settings(raw)["timeout_seconds"]
