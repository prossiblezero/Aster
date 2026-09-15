from settings import disabled_names


def plan_jobs(names, raw):
    disabled_names(raw)
    return list(names)
