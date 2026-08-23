"""Sensor data ingestion — MQTT subscriber and REST FastAPI router."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

try:
    import paho.mqtt.client as mqtt
    _MQTT_AVAILABLE = True
except ImportError:
    _MQTT_AVAILABLE = False
    logger.warning("paho-mqtt not installed; MQTT ingestion disabled.")

from config.settings import settings


# Type alias for the async callback registered by the orchestrator
SensorCallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class MQTTIngestionClient:
    """MQTT subscriber that forwards sensor payloads to a registered async callback.

    Topic pattern: ``ecoguard/sensors/<station_id>/<pollutant>``
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._callbacks: List[SensorCallback] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connected = False

    def register_callback(self, cb: SensorCallback) -> None:
        """Register an async function called for every valid sensor payload."""
        self._callbacks.append(cb)

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc == 0:
            topic = f"{settings.mqtt_topic_prefix}/#"
            client.subscribe(topic)
            self._connected = True
            logger.info("MQTT connected | broker={}:{} topic={}", settings.mqtt_broker_host, settings.mqtt_broker_port, topic)
        else:
            logger.error("MQTT connection refused | rc={}", rc)

    def _on_disconnect(self, client, userdata, rc, properties=None, reason=None) -> None:
        self._connected = False
        logger.warning("MQTT disconnected | rc={}", rc)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Invalid MQTT payload on {}: {}", msg.topic, exc)
            return

        # Enrich with topic-derived metadata
        parts = msg.topic.split("/")
        if len(parts) >= 3:
            payload.setdefault("station_id", parts[2])
        if len(parts) >= 4:
            payload.setdefault("pollutant_type", parts[3].upper())

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._dispatch(payload), self._loop)
        else:
            logger.warning("Event loop not available; dropping MQTT message on {}", msg.topic)

    async def _dispatch(self, payload: Dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                await cb(payload)
            except Exception as exc:
                logger.error("MQTT callback error: {}", exc)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the MQTT client in a background thread."""
        if not _MQTT_AVAILABLE:
            logger.warning("paho-mqtt unavailable; MQTT ingestion not started.")
            return

        self._loop = loop
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(settings.mqtt_broker_host, settings.mqtt_broker_port, keepalive=60)
            self._client.loop_start()
            logger.info("MQTT loop started | broker={}:{}", settings.mqtt_broker_host, settings.mqtt_broker_port)
        except Exception as exc:
            logger.error("Failed to connect to MQTT broker: {}", exc)

    def stop(self) -> None:
        """Gracefully disconnect from the MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
            logger.info("MQTT client stopped.")

    @property
    def is_connected(self) -> bool:
        return self._connected


# ── REST ingestion helper ────────────────────────────────────────────────────

async def process_rest_reading(payload: Dict[str, Any], callbacks: List[SensorCallback]) -> None:
    """Dispatch a REST-ingested sensor reading through the same callbacks as MQTT."""
    for cb in callbacks:
        try:
            await cb(payload)
        except Exception as exc:
            logger.error("REST ingestion callback error: {}", exc)
