from telegram_channel_tracker.ui import render_dashboard, render_home


def test_dashboard_does_not_expose_disabled_media_settings() -> None:
    html = render_dashboard()
    assert "Download media attachments" not in html
    assert "Media limit (MB)" not in html
    assert "Media retention (days)" not in html


def test_rule_actions_are_kept_in_a_non_wrapping_flex_group() -> None:
    html = render_dashboard()
    assert ".rule>div:last-child{display:flex;flex:0 0 auto;gap:6px;white-space:nowrap}" in html


def test_new_rule_editor_is_collapsed_and_separate_from_rule_list() -> None:
    html = render_dashboard()
    editor_position = html.index('id="rule-editor"')
    list_position = html.index('<h2>Keyword rules</h2>')
    assert editor_position < list_position
    assert '<details id="rule-editor" class="rule-editor">' in html
    assert '<summary id="rule-editor-label">Add new rule</summary>' in html
    assert '.rule-editor>summary{font-size:1.5em;line-height:1.2}' in html
    assert "$('#rule-editor').open=true" in html


def test_rule_editor_configures_email_and_telegram_notifications() -> None:
    html = render_dashboard()
    assert 'name="email_alerts" type="checkbox" checked' in html
    assert 'name="telegram_bot_alerts" type="checkbox" checked' in html
    assert 'Notifications: ${channels}' in html
    assert "email_alerts:f.get('email_alerts')==='on'" in html
    assert "telegram_bot_alerts:f.get('telegram_bot_alerts')==='on'" in html


def test_disabled_rules_are_grouped_and_collapsed_by_default() -> None:
    html = render_dashboard()
    assert '<div id="enabled-rules"></div>' in html
    assert '<details id="disabled-rules">' in html
    assert '<summary>Disabled rules (<span id="disabled-rule-count">0</span>)</summary>' in html
    assert 'h3,#disabled-rules>summary{font-size:1.17em;line-height:1.2}' in html
    assert "rulesCache.filter(r=>!r.enabled)" in html
    assert "$('#disabled-rules').hidden=!disabled.length" in html


def test_posts_section_uses_clear_heading() -> None:
    html = render_dashboard()
    assert '<h2>Posts</h2>' in html
    assert '<h2>Archived posts</h2>' not in html


def test_archive_refresh_has_visible_progress_and_result_feedback() -> None:
    html = render_dashboard()
    assert 'id="archive-status"' in html
    assert 'type="button">Refresh</button>' in html
    assert "$('#refresh').onclick=refreshPosts" in html
    assert "button.textContent='Refreshing…'" in html
    assert "status.textContent=`Showing ${posts.length} post" in html
    assert "cache:'no-store'" in html


def test_archive_can_filter_posts_from_n_days_ago() -> None:
    html = render_dashboard()
    assert 'id="days-ago"' in html
    assert 'placeholder="N"' in html
    assert '<span>days ago</span>' in html
    assert "q.set('days',$('#days-ago').value)" in html
    assert "$('#days-ago').oninput=schedulePosts" in html


def test_target_settings_are_a_compact_top_bar() -> None:
    html = render_dashboard()
    settings_position = html.index('class="target-settings"')
    rules_position = html.index("<h2>Keyword rules</h2>")
    assert settings_position < rules_position
    assert '.target-settings form{display:flex' in html
    assert '.target-settings input[type=number]{width:110px}' in html


def test_browser_alert_button_reports_permission_state_on_both_pages() -> None:
    for html in (render_home(), render_dashboard()):
        assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in html
        assert 'id="notify-status"' in html
        assert "async function requestBrowserAlerts()" in html
        assert "Notification.requestPermission()" in html
        assert "Browser alerts blocked" in html
        assert "Notifications are not supported" in html
        assert "initializeBrowserAlerts();" in html


def test_blocked_browser_alert_button_shows_unblocking_steps() -> None:
    html = render_dashboard()
    assert "button:disabled{cursor:not-allowed" in html
    assert 'button[data-loading="true"]{cursor:progress}' in html
    assert "if(permission==='denied'){button.disabled=false" in html
    assert "function showBrowserAlertHelp()" in html
    assert "Safari → Settings → Websites → Notifications" in html
    assert "Notification.permission==='denied'){showBrowserAlertHelp();return}" in html


def test_home_page_shows_email_alert_configuration() -> None:
    html = render_home()
    assert 'id="email-alerts"' in html
    assert 'id="email-status"' in html
    assert "email_configured" in html
    assert "telegram-channel-tracker setup-email" in html


def test_home_page_shows_telegram_bot_alert_configuration() -> None:
    html = render_home()
    assert 'id="bot-alerts"' in html
    assert 'id="bot-status"' in html
    assert "telegram_bot_configured" in html
    assert "telegram-channel-tracker setup-bot" in html
