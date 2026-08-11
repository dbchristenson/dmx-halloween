import logging

import pytest

from dmx_volcano.co2 import CO2Tracker
from dmx_volcano.notify import SmsConfig, send_sms, sms_config_from


@pytest.fixture
def tracker(tmp_path):
    return CO2Tracker(
        capacity_kg=10.0,
        rate_kg_per_s=0.5,
        state_path=tmp_path / "co2.json",
        warn_remaining_fraction=0.2,
    )


def test_usage_accumulates(tracker):
    tracker.record_burst(2.0)  # 1.0 kg
    tracker.record_burst(4.0)  # 2.0 kg
    assert tracker.used_kg == pytest.approx(3.0)
    assert tracker.remaining_kg == pytest.approx(7.0)
    assert tracker.remaining_fraction == pytest.approx(0.7)


def test_alert_fires_exactly_once_at_threshold(tracker):
    assert tracker.record_burst(15.0) is False  # 7.5 kg used, 25% left
    assert tracker.record_burst(2.0) is True  # 8.5 kg used, 15% left -> alert
    assert tracker.record_burst(1.0) is False  # already alerted, no re-alert
    assert tracker.alerted is True


def test_state_persists_across_restart(tracker, tmp_path):
    tracker.record_burst(16.5)
    tracker.record_burst(2.0)  # crosses threshold -> alerted
    reloaded = CO2Tracker(
        capacity_kg=10.0,
        rate_kg_per_s=0.5,
        state_path=tmp_path / "co2.json",
        warn_remaining_fraction=0.2,
    )
    assert reloaded.used_kg == pytest.approx(tracker.used_kg)
    assert reloaded.alerted is True  # a restart must not re-text everyone


def test_tank_reset(tracker):
    tracker.record_burst(18.0)
    tracker.reset_tank()
    assert tracker.used_kg == 0.0
    assert tracker.alerted is False
    assert tracker.remaining_fraction == 1.0


def test_unconfigured_sms_logs_and_never_raises(caplog):
    cfg = SmsConfig(numbers=["+15555550100"])
    with caplog.at_level(logging.WARNING):
        send_sms(cfg, "test alert")
    assert "not configured" in caplog.text


def test_sms_config_env_overrides_toml(monkeypatch):
    raw = {
        "alerts": {
            "phone_numbers": ["+15555550100", "+15555550101"],
            "twilio": {"account_sid": "toml_sid", "auth_token": "t", "from_number": "+1"},
        }
    }
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "env_sid")
    cfg = sms_config_from(raw)
    assert cfg.account_sid == "env_sid"
    assert cfg.auth_token == "t"  # falls back to toml where env unset
    assert cfg.numbers == ["+15555550100", "+15555550101"]
    assert cfg.configured
