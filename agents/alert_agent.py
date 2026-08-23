"""Regulatory Alert & Escalation Agent."""
from __future__ import annotations

import asyncio
import smtplib
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from loguru import logger

from agents.base_agent import BaseAgent
from config.settings import settings


class RegulatoryAlertAgent(BaseAgent):
    """Routes violation alerts through email, webhook, or dashboard channels.

    Routing rules:
        CRITICAL  → regulatory webhook + email
        HIGH      → email
        MEDIUM    → dashboard event
        LOW       → log only

    Rate limiting: at most 1 alert per (station, pollutant) pair per hour.
    """

    name = "RegulatoryAlertAgent"
    description = "Escalates violations via email/webhook with per-station rate limiting."

    # Max 1 alert per station+pollutant combination per hour (3600 seconds)
    RATE_LIMIT_SECONDS = 3600

    def __init__(self, orchestrator: Any) -> None:
        super().__init__(orchestrator)
        # last_alert_time[(station_id, pollutant_type)] = datetime
        self._last_alert: Dict[Tuple[str, str], datetime] = {}
        self._recipients: List[str] = [settings.alert_email_user]

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _is_rate_limited(self, station_id: str, pollutant: str) -> bool:
        key = (station_id, pollutant)
        last = self._last_alert.get(key)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < self.RATE_LIMIT_SECONDS

    def _record_alert(self, station_id: str, pollutant: str) -> None:
        self._last_alert[(station_id, pollutant)] = datetime.now(timezone.utc)

    # ── Email ─────────────────────────────────────────────────────────────────

    async def send_email_alert(
        self, violation: Dict[str, Any], recipients: Optional[List[str]] = None
    ) -> bool:
        """Send an SMTP email alert for a violation.

        Args:
            violation: Violation dict with station_id, pollutant_type, severity, etc.
            recipients: List of email addresses. Defaults to ``settings.alert_email_user``.

        Returns:
            True if sent successfully, False otherwise.
        """
        if recipients is None:
            recipients = self._recipients

        subject = (
            f"[EcoGuardAI] {violation.get('severity')} Violation — "
            f"{violation.get('pollutant_type')} at {violation.get('station_id')}"
        )
        body = (
            f"<h2>Environmental Violation Alert</h2>"
            f"<table>"
            f"<tr><td><b>Station</b></td><td>{violation.get('station_id')}</td></tr>"
            f"<tr><td><b>Pollutant</b></td><td>{violation.get('pollutant_type')}</td></tr>"
            f"<tr><td><b>Measured</b></td><td>{violation.get('measured_value')} {violation.get('unit', '')}</td></tr>"
            f"<tr><td><b>Limit</b></td><td>{violation.get('limit_value')} {violation.get('unit', '')}</td></tr>"
            f"<tr><td><b>Severity</b></td><td><strong>{violation.get('severity')}</strong></td></tr>"
            f"<tr><td><b>Time</b></td><td>{violation.get('timestamp')}</td></tr>"
            f"</table>"
            f"<hr><h3>Violation Report</h3>"
            f"<p>{violation.get('report_text', 'No report available.')}</p>"
            f"<p><em>This is an automated alert from EcoGuardAI. Do not reply.</em></p>"
        )

        loop = asyncio.get_event_loop()

        def _send() -> bool:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = settings.alert_email_user
                msg["To"] = ", ".join(recipients)
                msg.attach(MIMEText(body, "html"))

                with smtplib.SMTP(settings.alert_email_smtp, settings.alert_email_port) as server:
                    server.starttls()
                    server.login(settings.alert_email_user, settings.alert_email_password)
                    server.sendmail(settings.alert_email_user, recipients, msg.as_string())
                return True
            except Exception as exc:
                logger.error("Email send failed: {}", exc)
                return False

        return await loop.run_in_executor(None, _send)

    # ── Webhook ───────────────────────────────────────────────────────────────

    async def send_webhook_alert(
        self, violation: Dict[str, Any], url: Optional[str] = None
    ) -> bool:
        """POST a JSON webhook alert to the regulatory endpoint.

        Args:
            violation: Violation dict.
            url: Override webhook URL; defaults to ``settings.regulatory_webhook_url``.

        Returns:
            True if the webhook returned HTTP 2xx, False otherwise.
        """
        target_url = url or settings.regulatory_webhook_url
        payload = {
            "source": "EcoGuardAI",
            "event": "violation.detected",
            "severity": violation.get("severity"),
            "station_id": violation.get("station_id"),
            "pollutant_type": violation.get("pollutant_type"),
            "measured_value": violation.get("measured_value"),
            "limit_value": violation.get("limit_value"),
            "unit": violation.get("unit"),
            "timestamp": str(violation.get("timestamp")),
            "report": violation.get("report_text", ""),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    target_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status < 300:
                        self.log(f"Webhook sent | url={target_url} status={resp.status}")
                        return True
                    self.log(f"Webhook returned {resp.status}", level="warning")
                    return False
        except Exception as exc:
            logger.error("Webhook send failed: {}", exc)
            return False

    # ── Core logic ────────────────────────────────────────────────────────────

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single violation and dispatch the appropriate alert."""
        violation = context
        station_id = violation.get("station_id", "UNKNOWN")
        pollutant = violation.get("pollutant_type", "UNKNOWN")
        severity = violation.get("severity", "LOW")

        if self._is_rate_limited(station_id, pollutant):
            self.log(f"Rate-limited | station={station_id} pollutant={pollutant}", level="debug")
            return {"status": "rate_limited"}

        self._record_alert(station_id, pollutant)

        if severity == "CRITICAL":
            results = await asyncio.gather(
                self.send_email_alert(violation),
                self.send_webhook_alert(violation),
                return_exceptions=True,
            )
            email_ok = results[0] is True
            webhook_ok = results[1] is True
            self.log(f"CRITICAL alert dispatched | station={station_id}")
        elif severity == "HIGH":
            email_ok = await self.send_email_alert(violation)
            webhook_ok = False
            self.log(f"HIGH alert emailed | station={station_id}")
        elif severity == "MEDIUM":
            await self.orchestrator.emit("dashboard.alert", violation)
            email_ok = False
            webhook_ok = False
            self.log(f"MEDIUM alert -> dashboard | station={station_id}")
        else:
            # LOW violations are log-only — nothing dispatched, nothing persisted.
            self.log(f"LOW violation logged | station={station_id}")
            email_ok = webhook_ok = False

        # ── Step 1 persistence hook (fail-safe, additive) ──────────────────────
        # Persist ONLY actually-dispatched alerts; suppressed / rate-limited
        # events never reach this point (early return above).
        try:
            from services.persistence import persist_alerts_for_dispatch

            dispatched: List[Tuple[str, str, bool]] = []
            if severity == "CRITICAL":
                dispatched.append(("EMAIL", settings.alert_email_user, email_ok))
                dispatched.append(("WEBHOOK", settings.regulatory_webhook_url, webhook_ok))
            elif severity == "HIGH":
                dispatched.append(("EMAIL", settings.alert_email_user, email_ok))
            elif severity == "MEDIUM":
                dispatched.append(("DASHBOARD", "dashboard", True))

            if dispatched:
                await persist_alerts_for_dispatch(violation, dispatched)
        except Exception as exc:
            logger.error("Alert persistence hook error (dispatch unaffected): {}", exc)

        return {"status": "dispatched", "severity": severity}

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle a ``violation.detected`` event."""
        await self.run(event)