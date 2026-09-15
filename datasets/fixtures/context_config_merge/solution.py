from copy import deepcopy


def merge_config(base, override):
    result = deepcopy(base)
    result.update(deepcopy(override))
    return result
