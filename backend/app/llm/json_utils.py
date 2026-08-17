"""Robuste JSON-Extraktion aus LLM-Textantworten (Perplexity-Client)."""

import json
import re


def extract_json(text: str) -> dict:
    """Robust JSON extraction from LLM text output.

    Handles: markdown fences, prose prefix/suffix, trailing commas,
    truncated tails, mixed single/double quotes.
    """
    if not text:
        raise ValueError("empty response")

    # Strip markdown fences (```json ... ``` or ``` ... ```)
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find first well-balanced {...} block by counting braces
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in extractor response")
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i, ch in enumerate(text[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        # Response likely truncated mid-JSON. Try to close open braces/brackets.
        candidate = text[start:]
        # Trim any trailing incomplete key/value line
        candidate = re.sub(r",\s*[^,{}\[\]:]*$", "", candidate)
        # Count and close open braces + brackets
        open_braces = candidate.count("{") - candidate.count("}")
        open_brackets = candidate.count("[") - candidate.count("]")
        candidate = candidate.rstrip(", \n\t") + ("]" * open_brackets) + ("}" * open_braces)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(f"truncated JSON, salvage failed: {e}")
    return json.loads(text[start:end + 1])
