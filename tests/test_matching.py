from telegram_channel_tracker.matching import RuleSpec, matches


def test_any_terms_are_unicode_case_insensitive() -> None:
    assert matches("A CAFÉ opened today", RuleSpec(("café", "closed")))


def test_all_terms_and_exclusions() -> None:
    rule = RuleSpec(("launch", "today"), "all", ("rumor",))
    assert matches("Official launch is today", rule)
    assert not matches("Launch is tomorrow", rule)
    assert not matches("Rumor: official launch today", rule)


def test_empty_include_never_matches() -> None:
    assert not matches("anything", RuleSpec(("",)))

