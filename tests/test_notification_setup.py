import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from telegram_channel_tracker.app import create_app
from telegram_channel_tracker.notification_setup import NotificationSetup, SetupError
from telegram_channel_tracker.settings import Settings, load_settings, save_settings
import telegram_channel_tracker.notification_setup as module


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, gmail_address='old@gmail.com', email_recipient='old@example.com',
                        telegram_bot_username='old_bot', telegram_bot_chat_id=12)
    save_settings(settings)
    service = NotificationSetup(settings)
    monkeypatch.setattr(module.GmailAlertSender, 'send', AsyncMock())
    monkeypatch.setattr(module, 'load_gmail_app_password', lambda _: 'stored-secret')
    monkeypatch.setattr(module, 'load_bot_token', lambda _: 'stored-token')
    service._stores = []
    monkeypatch.setattr(module, 'store_gmail_app_password', lambda account, secret: service._stores.append((account, secret)))
    monkeypatch.setattr(module, 'store_bot_token', lambda account, secret: service._stores.append((account, secret)))
    monkeypatch.setattr(service, '_delete_staged', AsyncMock())
    return service


def test_email_test_before_save_and_live_settings_update(setup, monkeypatch):
    old_object = setup.settings
    old_disk = setup.settings.config_path.read_text()
    async def send(*args, **kwargs):
        assert setup.settings.config_path.read_text() == old_disk
        assert not setup._stores
        assert kwargs['password'] == 'app-password'
    monkeypatch.setattr(module.GmailAlertSender, 'send', send)
    asyncio.run(setup.save_email('new@gmail.com', 'new@example.com', 'app-password'))
    assert setup.settings is old_object
    assert old_object.gmail_address == 'new@gmail.com'
    assert old_object.email_recipient == 'new@example.com'
    assert setup._stores[0][0] == old_object.gmail_keychain_account
    assert 'app-password' not in old_object.config_path.read_text()
    assert old_object.config_path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv('TCT_DATA_DIR', str(old_object.data_dir))
    assert load_settings().gmail_keychain_account == old_object.gmail_keychain_account


def test_email_reuses_stored_secret(setup):
    asyncio.run(setup.save_email('old@gmail.com', 'new@example.com', ''))
    assert setup._stores[0][1] == 'stored-secret'
    asyncio.run(setup.test_email())
    assert module.GmailAlertSender.send.await_count == 2


@pytest.mark.parametrize('stage', ['test', 'keychain', 'save'])
def test_email_failure_preserves_previous_configuration(setup, monkeypatch, stage):
    previous = asdict(setup.settings)
    disk = setup.settings.config_path.read_text()
    def fail(*args, **kwargs):
        raise RuntimeError('sensitive-secret')
    if stage == 'test':
        monkeypatch.setattr(module.GmailAlertSender, 'send', AsyncMock(side_effect=RuntimeError('sensitive-secret')))
    elif stage == 'keychain':
        monkeypatch.setattr(module, 'store_gmail_app_password', fail)
    else:
        monkeypatch.setattr(module, 'save_settings', fail)
    with pytest.raises(SetupError) as exc:
        asyncio.run(setup.save_email('new@gmail.com', '', 'sensitive-secret'))
    assert 'sensitive-secret' not in str(exc.value)
    assert asdict(setup.settings) == previous
    assert setup.settings.config_path.read_text() == disk


@pytest.mark.parametrize('sender,password', [('bad\naddress', 'password'), ('new@gmail.com', '')])
def test_invalid_email_never_tests_or_saves(setup, sender, password):
    with pytest.raises(SetupError):
        asyncio.run(setup.save_email(sender, '', password))
    assert not setup._stores
    module.GmailAlertSender.send.assert_not_awaited()


def bot_api(setup, monkeypatch, messages=None, fail_send=False):
    calls = []
    def call(token, method, payload=None):
        calls.append((token, method, payload))
        if method == 'getMe':
            return {'username': 'new_bot', 'is_bot': True}
        if method == 'getWebhookInfo':
            return {'url': ''}
        if method == 'getUpdates':
            connection = next(iter(setup.connections.values()))
            return messages if messages is not None else [
                {'message': {'chat': {'type': 'private', 'id': 999}, 'text': '/start unrelated'}},
                {'message': {'chat': {'type': 'private', 'id': 42}, 'text': '/start '+connection.code}},
            ]
        if method == 'sendMessage' and fail_send:
            raise RuntimeError('secret-token')
        return {}
    monkeypatch.setattr(module, 'call_bot_api', call)
    return calls


def test_bot_connect_matches_code_then_saves_and_retests(setup, monkeypatch):
    calls = bot_api(setup, monkeypatch)
    async def exercise():
        connection = await setup.begin_bot('secret-token')
        assert 'secret-token' not in json.dumps(connection)
        assert not setup._stores
        await setup.finish_bot(connection['connection_id'])
        assert setup.settings.telegram_bot_chat_id == 42
        assert setup.settings.telegram_bot_username == 'new_bot'
        assert not setup.connections
        await setup.test_bot()
    asyncio.run(exercise())
    sends = [c for c in calls if c[1] == 'sendMessage']
    assert len(sends) == 2
    assert sends[0][2]['chat_id'] == 42
    assert 'secret-token' not in setup.settings.config_path.read_text()


@pytest.mark.parametrize('scenario', ['missing', 'expired', 'unrelated', 'send_failure'])
def test_bot_setup_failures_leave_settings_unchanged(setup, monkeypatch, scenario):
    messages = [{'message': {'chat': {'type': 'private', 'id': 99}, 'text': '/start wrong'}}] if scenario == 'unrelated' else ([] if scenario == 'missing' else None)
    bot_api(setup, monkeypatch, messages, scenario == 'send_failure')
    disk = setup.settings.config_path.read_text()
    async def exercise():
        result = await setup.begin_bot('secret-token')
        if scenario == 'expired':
            setup.connections[result['connection_id']].expires = 0
        with pytest.raises(SetupError):
            await setup.finish_bot(result['connection_id'])
    asyncio.run(exercise())
    assert setup.settings.config_path.read_text() == disk
    assert not setup._stores


def test_bot_invalid_token_is_sanitized(setup, monkeypatch):
    def fail(*args):
        raise RuntimeError('secret-token')
    monkeypatch.setattr(module, 'call_bot_api', fail)
    with pytest.raises(SetupError) as exc:
        asyncio.run(setup.begin_bot('secret-token'))
    assert 'secret-token' not in str(exc.value)
    assert not setup.connections


def test_notification_api_guards_secrets_and_origin(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get('/settings').status_code == 200
        assert client.get('/api/notifications').headers['cache-control'] == 'no-store'
        payload = {'sender': 'bad', 'password': 'private-password'}
        assert client.post('/api/notifications/email/setup', json=payload).status_code == 403
        assert client.post('/api/notifications/email/setup', json=payload, headers={'origin': 'https://evil.example'}).status_code == 403
        response = client.post('/api/notifications/email/setup', json=payload, headers={'origin': 'http://testserver'})
        assert response.status_code == 400
        assert 'private-password' not in response.text
        response = client.post('/api/notifications/email/setup', json={'password': ['private-password']}, headers={'origin': 'http://testserver'})
        assert response.status_code == 400
        assert 'private-password' not in response.text
        assert client.post('/api/notifications/email/test', content='{}', headers={'origin': 'http://testserver', 'content-type': 'text/plain'}).status_code == 415


def test_atomic_settings_write_preserves_previous_file(tmp_path, monkeypatch):
    import telegram_channel_tracker.settings as settings_module
    settings = Settings(data_dir=tmp_path)
    save_settings(settings)
    previous = settings.config_path.read_text()
    monkeypatch.setattr(settings_module.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError()))
    settings.gmail_address = 'new@gmail.com'
    with pytest.raises(OSError):
        save_settings(settings)
    assert settings.config_path.read_text() == previous
    assert not list(tmp_path.glob('.config-*'))


def test_saved_setup_stays_paired_with_credentials_after_restart(setup, monkeypatch):
    asyncio.run(setup.save_email('new@gmail.com', 'new@example.com', 'app-password'))
    monkeypatch.setenv('TCT_DATA_DIR', str(setup.settings.data_dir))
    monkeypatch.setenv('TCT_GMAIL_ADDRESS', 'env@gmail.com')
    monkeypatch.setenv('TCT_EMAIL_RECIPIENT', 'env@example.com')
    reloaded = load_settings()
    assert reloaded.gmail_address == 'new@gmail.com'
    assert reloaded.email_recipient == 'new@example.com'


def test_credentials_use_staged_account_and_support_legacy_accounts(setup, monkeypatch):
    from telegram_channel_tracker.email_alerts import load_gmail_app_password
    from telegram_channel_tracker.bot_alerts import load_bot_token
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='keychain-secret')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.delenv('TCT_GMAIL_APP_PASSWORD', raising=False)
    monkeypatch.delenv('TCT_TELEGRAM_BOT_TOKEN', raising=False)
    assert load_gmail_app_password(setup.settings) == 'keychain-secret'
    assert 'old@gmail.com' in calls[-1]
    assert load_bot_token(setup.settings) == 'keychain-secret'
    assert 'old_bot' in calls[-1]
    setup.settings.gmail_keychain_account = 'tracker-email'
    setup.settings.bot_keychain_account = 'tracker-bot'
    monkeypatch.setenv('TCT_GMAIL_APP_PASSWORD', 'stale-env-secret')
    monkeypatch.setenv('TCT_TELEGRAM_BOT_TOKEN', 'stale-env-token')
    assert load_gmail_app_password(setup.settings) == 'keychain-secret'
    assert 'tracker-email' in calls[-1]
    assert load_bot_token(setup.settings) == 'keychain-secret'
    assert 'tracker-bot' in calls[-1]


def test_api_updates_shared_monitor_settings_and_can_test_saved_setup(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, api_id=1, api_hash='hash')
    fake_monitor = SimpleNamespace(settings=settings, run=AsyncMock(), stop=AsyncMock())
    sent = AsyncMock()
    monkeypatch.setattr(module.GmailAlertSender, 'send', sent)
    monkeypatch.setattr(module, 'store_gmail_app_password', lambda *_: None)
    with TestClient(create_app(settings, monitor=fake_monitor)) as client:
        response = client.post('/api/notifications/email/setup', headers={'origin': 'http://testserver'}, json={
            'sender': 'new@gmail.com', 'recipient': 'new@example.com', 'password': 'private-secret',
        })
        assert response.status_code == 200
        assert fake_monitor.settings.gmail_address == 'new@gmail.com'
        assert 'private-secret' not in response.text
        assert 'keychain_account' not in response.text
        assert client.post('/api/notifications/email/test', headers={'origin': 'http://testserver'}, json={}).status_code == 200
    assert sent.await_count == 2


@pytest.mark.parametrize('stage', ['keychain', 'save'])
def test_bot_persistence_failure_preserves_configuration(setup, monkeypatch, stage):
    bot_api(setup, monkeypatch)
    disk = setup.settings.config_path.read_text()
    def fail(*args):
        raise OSError('private-secret')
    monkeypatch.setattr(module, 'store_bot_token' if stage == 'keychain' else 'save_settings', fail)
    async def exercise():
        connection = await setup.begin_bot('private-secret')
        with pytest.raises(SetupError) as error:
            await setup.finish_bot(connection['connection_id'])
        assert 'private-secret' not in str(error.value)
    asyncio.run(exercise())
    assert setup.settings.telegram_bot_username == 'old_bot'
    assert setup.settings.config_path.read_text() == disk
