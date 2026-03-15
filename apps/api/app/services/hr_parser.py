"""Extract HR-related entities from free text."""

import re

ROLES = [
    "bartender", "barista", "chef", "head chef", "sous chef",
    "kitchen hand", "dishwasher", "waiter", "waitress", "host",
    "hostess", "manager", "duty manager", "floor manager",
    "bar manager", "kitchen manager", "server",
]

DAYS = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


def extract_role(text: str) -> str | None:
    """Match against known hospitality roles. Longest match wins."""
    text_lower = text.lower()
    best = None
    best_len = 0
    for role in ROLES:
        if role in text_lower and len(role) > best_len:
            best = role
            best_len = len(role)
    return best


def extract_employee_name(text: str) -> str | None:
    """Try to extract a person's name from a full sentence.

    Looks for patterns like "set up <Name>" or "<Name> as a <role>".
    """
    # Pattern: "set up <First> <Last>"
    m = re.search(r"set\s*up\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
    if m:
        return m.group(1)

    # Pattern: "<First> <Last> as a"
    m = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s+as\s+a?\b", text)
    if m:
        return m.group(1)

    return None


def extract_name_from_short_reply(text: str) -> str | None:
    """Extract a name from a short follow-up reply.

    Handles replies like:
    - "Sarah Jones, bartender"
    - "Sarah Jones"
    - "her name is Sarah Jones"
    - "It's John Smith"
    """
    text = text.strip()

    # "her name is <Name>" / "name is <Name>" / "it's <Name>" / "they're called <Name>"
    m = re.search(r"(?:name\s+is|it'?s|called)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text)
    if m:
        return m.group(1)

    # Short reply: "<First> <Last>" possibly followed by comma + role
    m = re.match(r"^([A-Z][a-z]+\s+[A-Z][a-z]+)", text)
    if m:
        return m.group(1)

    # Single capitalized word (first name only)
    m = re.match(r"^([A-Z][a-z]{2,})(?:\s|,|$)", text)
    if m and m.group(1).lower() not in [r for r in ROLES]:
        return m.group(1)

    return None


def extract_start_date(text: str) -> str | None:
    """Extract a relative start date reference."""
    text_lower = text.lower()

    # "next week"
    if "next week" in text_lower:
        return "next week"

    # "next <day>"
    for day in DAYS:
        if f"next {day}" in text_lower:
            return f"next {day}"

    # "starts/starting <day>"
    m = re.search(r"(?:starts?|starting)\s+(" + "|".join(DAYS) + r")", text_lower)
    if m:
        return m.group(1).capitalize()

    # "starts/starting on <day>"
    m = re.search(r"(?:starts?|starting)\s+on\s+(" + "|".join(DAYS) + r")", text_lower)
    if m:
        return m.group(1).capitalize()

    return None
