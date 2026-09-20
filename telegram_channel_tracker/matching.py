from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import cache
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



def matched_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Map normalized phrase occurrences back to original character offsets."""
    haystack = normalize(text)

    @cache
    def prefix_length(index: int) -> int:
        return len(normalize(text[:index]))

    boundaries = range(len(text) + 1)
    spans: list[tuple[int, int]] = []
    for term in {normalize(term.strip()) for term in terms if term.strip()}:
        offset = haystack.find(term)
        while offset != -1:
            start = bisect_right(boundaries, offset, key=prefix_length) - 1
            end = bisect_left(boundaries, offset + len(term), key=prefix_length)
            # Include combining characters that compose into the final character.
            end = bisect_right(boundaries, prefix_length(end), key=prefix_length) - 1
            spans.append((start, end))
            offset = haystack.find(term, offset + 1)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
