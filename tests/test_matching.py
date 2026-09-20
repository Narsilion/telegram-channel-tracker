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


def test_highlights_repeated_and_overlapping_phrases() -> None:
    from telegram_channel_tracker.matching import matched_spans

    assert matched_spans('Product launch, LAUNCH!', ['product launch', 'launch', 'absent']) == [(0, 14), (16, 22)]
    assert matched_spans('banana', ['ana']) == [(1, 6)]
    assert matched_spans('unchanged', []) == []


def test_highlights_preserve_unicode_character_boundaries() -> None:
    from telegram_channel_tracker.matching import matched_spans

    text = '😀 Straße Cafe\u0301 ＬＡＵＮＣＨ'
    spans = matched_spans(text, ['STRASSE', 'café', 'launch'])
    assert [text[start:end] for start, end in spans] == ['Straße', 'Cafe\u0301', 'ＬＡＵＮＣＨ']

