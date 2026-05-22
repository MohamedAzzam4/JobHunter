"""
Utility for extracting JSON objects from AI-generated text.

AI models often return JSON wrapped in markdown code blocks or mixed with
explanatory text. This module provides robust extraction using brace counting.
"""

import json


def extract_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object from text.

    Uses brace counting instead of greedy regex to avoid
    capturing text between unrelated braces.
    """
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    # Keep searching for next { after this failed one
                    next_start = text.find('{', start + 1)
                    if next_start != -1:
                        return extract_json_object(text[next_start:])
                    return None
    return None
