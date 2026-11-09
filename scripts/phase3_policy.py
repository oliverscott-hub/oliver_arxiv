"""Pure validation and scoring for the phase 3 forced-choice experiments."""

import re

WEIGHTS = (15, 30, 15, 10, 20, 10)
POLICIES = ("standard", "q1_no_u", "all_no_u")


def allowed_choices(policy: str) -> tuple[str, ...]:
    if policy not in POLICIES:
        raise ValueError(f"Unknown phase 3 policy: {policy}")
    return tuple("ABCD" if policy == "all_no_u" or (policy == "q1_no_u" and i == 1)
                 else "ABCDU" for i in range(1, 7))


def grammar_for(policy: str) -> str:
    choices = allowed_choices(policy)
    root = '"Q1=" choice1' + ''.join(f' "\\nQ{i}=" choice{i}' for i in range(2, 7))
    rules = "\n".join(f"choice{i} ::= [{choice}]" for i, choice in enumerate(choices, 1))
    return f"root ::= {root}\n{rules}\n"


def parse_response(raw: str, policy: str) -> tuple[tuple[str, ...], int | None]:
    choices_allowed = allowed_choices(policy)
    pattern = "\\n".join(f"Q{i}=([{allowed}])" for i, allowed in enumerate(choices_allowed, 1))
    match = re.fullmatch(pattern, raw.strip())
    if not match:
        raise ValueError("Expected six permitted single-letter Q answers")
    choices = match.groups()
    if "U" in choices:
        return choices, None
    weighted = sum(weight * "ABCD".index(choice) for weight, choice in zip(WEIGHTS, choices))
    return choices, (weighted * 10 + 1) // 3
