"""Telegram failure classification and durable delivery recovery."""

from pathlib import Path

import httpx
import pytest

from notifier import check_alerts as notifier
from tests.test_unit import ALERT, _client


def test_photo_timeout_never_falls_back_to_text():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout('uncertain', request=request)
    with pytest.raises(httpx.ReadTimeout):
        notifier.send_alert(_client(handler), 'TOKEN', '42', ALERT)
    assert calls == ['/botTOKEN/sendPhoto']


def test_photo_server_error_never_falls_back_to_text():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500)
    with pytest.raises(httpx.HTTPStatusError) as error:
        notifier.send_alert(_client(handler), 'TOKEN', '42', ALERT)
    assert notifier.delivery_status(error.value) == 'uncertain'
    assert calls == ['/botTOKEN/sendPhoto']


def test_telegram_ok_false_is_not_a_success():
    with pytest.raises(notifier.TelegramRejected):
        notifier.send_alert(_client(lambda r: httpx.Response(200, json={'ok': False})),
                            'TOKEN', '42', {**ALERT, 'image_url': None})


def test_empty_scope_needs_no_connection_or_credentials():
    assert notifier.deliver_alerts('unused', None, None, brand_ids=[]) == {'sent': 0, 'failed': 0, 'uncertain': 0}


def test_sender_does_not_log_token_bearing_request_urls(caplog):
    import logging
    caplog.set_level(logging.INFO, logger='httpx')
    notifier.send_alert(_client(lambda r: httpx.Response(200, json={'ok': True})),
                        'PRIVATE_TOKEN', '42', ALERT)
    assert 'PRIVATE_TOKEN' not in caplog.text


# Import the existing real database fixture, which uses a throwaway test database.
from tests.test_api import admin
from tests.conftest import TEST_DATABASE_URL

db = pytest.mark.skipif(not TEST_DATABASE_URL, reason='TEST_DATABASE_URL not set')


@pytest.fixture
def delivery_db(admin, monkeypatch):
    schema = Path(__file__).resolve().parent.parent / 'sql' / 'alert_delivery_schema.sql'
    admin.execute(schema.read_text())
    admin.execute(schema.read_text())
    admin.execute("INSERT INTO app.alerts (variant_id) VALUES (12), (61)")
    admin.execute('UPDATE catalog.variants SET available = true WHERE id = 12')
    monkeypatch.setattr(notifier.time, 'sleep', lambda _: None)
    return admin


@db
def test_scoped_send_is_recorded_once(delivery_db, monkeypatch):
    calls = []
    monkeypatch.setattr(notifier, 'send_alert', lambda *args: calls.append(args[-1]['brand_name']) or 123)
    kwargs = dict(brand_ids=[1], run_id='r1')
    assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', **kwargs)['sent'] == 1
    assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', **kwargs)['sent'] == 1
    assert calls == ['Fugazee']
    assert delivery_db.execute('SELECT status, telegram_message_id FROM app.alert_deliveries').fetchone() == {'status': 'sent', 'telegram_message_id': 123}
    assert delivery_db.execute('SELECT status FROM app.alerts WHERE variant_id = 61').fetchone()['status'] == 'active'


@db
def test_ambiguous_send_stays_uncertain_across_runs(delivery_db, monkeypatch):
    calls = []
    def timeout(*args):
        calls.append(1)
        raise httpx.ReadTimeout('may have arrived')
    monkeypatch.setattr(notifier, 'send_alert', timeout)
    for run_id in ['r1', 'r2']:
        assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', brand_ids=[1], run_id=run_id)['uncertain'] == 1
    assert calls == [1]
    assert delivery_db.execute('SELECT status FROM app.alert_deliveries').fetchone()['status'] == 'uncertain'
    assert delivery_db.execute('SELECT status FROM app.alerts WHERE variant_id = 12').fetchone()['status'] == 'active'


@db
def test_killed_sender_is_recovered_without_resending(delivery_db, monkeypatch):
    delivery_db.execute("INSERT INTO app.alert_deliveries (alert_id, run_id, status) "
                        "SELECT id, 'old', 'sending' FROM app.alerts WHERE variant_id = 12")
    monkeypatch.setattr(notifier, 'send_alert', lambda *args: pytest.fail('must not resend'))
    assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', brand_ids=[1])['uncertain'] == 1


@db
def test_definite_rejection_can_retry(delivery_db, monkeypatch):
    response = httpx.Response(403, request=httpx.Request('POST', 'https://telegram.invalid'))
    def reject(*args):
        response.raise_for_status()
    monkeypatch.setattr(notifier, 'send_alert', reject)
    assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', brand_ids=[1])['failed'] == 1
    monkeypatch.setattr(notifier, 'send_alert', lambda *args: 456)
    assert notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', brand_ids=[1])['sent'] == 1
    assert delivery_db.execute('SELECT attempts FROM app.alert_deliveries').fetchone()['attempts'] == 2


@db
def test_second_sender_cannot_run_concurrently(delivery_db):
    delivery_db.execute('SELECT pg_advisory_lock(%s)', (notifier.SENDER_LOCK,))
    try:
        with pytest.raises(RuntimeError, match='another Telegram sender'):
            notifier.deliver_alerts(TEST_DATABASE_URL, 'TOKEN', '42', brand_ids=[1])
    finally:
        delivery_db.execute('SELECT pg_advisory_unlock(%s)', (notifier.SENDER_LOCK,))
