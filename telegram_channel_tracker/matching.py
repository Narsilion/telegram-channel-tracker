from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True, slots=True)
class RuleSpec:
    include_terms: tuple[str, ...]
    match_mode: str = "any"
    exclude_terms: tuple[str, ...] = ()


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def matches(text: str, rule: RuleSpec) -> bool:
    haystack = normalize(text)
    included = [normalize(term.strip()) for term in rule.include_terms if term.strip()]
    excluded = [normalize(term.strip()) for term in rule.exclude_terms if term.strip()]
    if not included or any(term in haystack for term in excluded):
        return False
    checks = [term in haystack for term in included]
    return all(checks) if rule.match_mode == "all" else any(checks)

