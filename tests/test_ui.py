from telegram_channel_tracker.ui import render_dashboard


def test_dashboard_does_not_expose_disabled_media_settings() -> None:
    html = render_dashboard()
    assert "Download media attachments" not in html
    assert "Media limit (MB)" not in html
    assert "Media retention (days)" not in html


def test_rule_actions_are_kept_in_a_non_wrapping_flex_group() -> None:
    html = render_dashboard()
    assert ".rule>div:last-child{display:flex;flex:0 0 auto;gap:6px;white-space:nowrap}" in html
