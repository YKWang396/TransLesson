"""Conservative display notation for explicit spoken arithmetic; never evaluate."""
from __future__ import annotations

import re

_NUMBERS = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(), map(str, range(21))))
_OPERAND = r"(?:\d+(?:\.\d+)?|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r"|[A-Za-z])"
_ENGLISH = re.compile(r"(?<![A-Za-z0-9_])("+_OPERAND+r")\s+(plus|minus|times|equals|is equal to)\s+("+_OPERAND+r")(?![A-Za-z0-9_])", re.I)
_CHINESE = re.compile(r"(?<![A-Za-z0-9_])([0-9]+(?:\.[0-9]+)?|[A-Za-z])\s*(加上|加|减去|减|乘以|乘|等于)\s*([0-9]+(?:\.[0-9]+)?|[A-Za-z])(?![A-Za-z0-9_])")
_SIGNS = {"plus": "+", "minus": "−", "times": "×", "equals": "=", "is equal to": "=",
          "加上": "+", "加": "+", "减去": "−", "减": "−", "乘以": "×", "乘": "×", "等于": "="}


def display_math(text):
    """Only replace explicit binary operations, leaving fractions/scope to AI."""
    if re.search(r"\b(?:over|divided by)\b|分之|除以", text, re.I):
        return text
    def replace(match):
        left, op, right = match.groups()
        return _NUMBERS.get(left.lower(), left)+_SIGNS[op.lower()]+_NUMBERS.get(right.lower(), right)
    for _ in range(8):
        changed = _CHINESE.sub(replace, _ENGLISH.sub(replace, text))
        if changed == text:
            return text
        text = changed
    return text
