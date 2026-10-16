import re
from difflib import SequenceMatcher


def normalize(value):
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def similarity(message, example):
    message, example = normalize(message), normalize(example)
    if message == example:
        return 1.0
    if not message or not example:
        return 0.0
    left, right = set(message.split()), set(example.split())
    overlap = 2 * len(left & right) / (len(left) + len(right))
    return max(overlap, SequenceMatcher(None, message, example).ratio())
