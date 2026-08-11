"""SMS alerts via Twilio's REST API — stdlib only, no SDK dependency.

Recipient numbers are the hardcoded operator list in config ([alerts]
phone_numbers, E.164 format). Credentials come from the environment
(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER) or from
[alerts.twilio] in config; the environment wins. NEVER commit real
credentials — on the Pi they live in the systemd EnvironmentFile.

Unconfigured (the default on dev machines) -> the message is logged and
dropped, so simulation can never text anyone.
"""

import base64
import logging
import os
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsConfig:
    numbers: list[str] = field(default_factory=list)
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)


def sms_config_from(raw: dict) -> SmsConfig:
    alerts = raw.get("alerts", {})
    twilio = alerts.get("twilio", {})
    return SmsConfig(
        numbers=list(alerts.get("phone_numbers", [])),
        account_sid=os.environ.get("TWILIO_ACCOUNT_SID", twilio.get("account_sid", "")),
        auth_token=os.environ.get("TWILIO_AUTH_TOKEN", twilio.get("auth_token", "")),
        from_number=os.environ.get("TWILIO_FROM_NUMBER", twilio.get("from_number", "")),
    )


def send_sms(cfg: SmsConfig, message: str) -> None:
    """Blocking send to every configured number. Failures are logged, never
    raised — an SMS outage must not take down the show loop."""
    if not cfg.configured or not cfg.numbers:
        logger.warning("SMS not configured; dropping alert: %s", message)
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.account_sid}/Messages.json"
    auth = base64.b64encode(f"{cfg.account_sid}:{cfg.auth_token}".encode()).decode()
    for number in cfg.numbers:
        data = urllib.parse.urlencode(
            {"To": number, "From": cfg.from_number, "Body": message}
        ).encode()
        request = urllib.request.Request(
            url, data=data, headers={"Authorization": f"Basic {auth}"}
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            logger.info("SMS sent to %s", number)
        except Exception:
            logger.exception("SMS to %s failed", number)


def send_sms_async(cfg: SmsConfig, message: str) -> None:
    """Fire-and-forget from the show loop: never block a DMX frame on HTTP."""
    threading.Thread(target=send_sms, args=(cfg, message), daemon=True).start()
