def load_settings(raw):
    return {"timeout_seconds": raw.get("timeout_seconds", 2)}
