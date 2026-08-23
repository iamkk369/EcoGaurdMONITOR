"""Application settings loaded from environment variables via pydantic-settings."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for EcoGuardAI."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── IBM Cloud / WatsonX ──────────────────────────────────────────────────
    ibm_cloud_api_key: str = "dev-placeholder"
    ibm_cloud_region: str = "us-south"
    ibm_watson_url: str = "https://us-south.ml.cloud.ibm.com"
    ibm_granite_model_id: str = "ibm/granite-4-h-small"
    watsonx_project_id: str = "dev-placeholder"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql://ecoguard:ecoguard@localhost:5432/ecoguardai"

    # ── MQTT ─────────────────────────────────────────────────────────────────
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_topic_prefix: str = "ecoguard/sensors"

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Security ─────────────────────────────────────────────────────────────
    secret_key: str = "change_me_in_production"
    # Comma-separated string so env var can be ALLOWED_ORIGINS=http://a.com,http://b.com
    allowed_origins_raw: str = "http://localhost:3000,http://localhost:8000"
    # Comma-separated API keys enforced in production (ENVIRONMENT=production).
    # Leave empty for development — all requests are allowed through in dev mode.
    # Example: VALID_API_KEYS=key-abc123,key-def456
    valid_api_keys_raw: str = ""

    # ── Email alerts ─────────────────────────────────────────────────────────
    alert_email_smtp: str = "smtp.gmail.com"
    alert_email_port: int = 587
    alert_email_user: str = "alerts@ecoguardai.com"
    alert_email_password: str = ""

    # ── Regulatory webhook ───────────────────────────────────────────────────
    regulatory_webhook_url: str = "https://your-regulatory-endpoint.com/webhook"

    # ── Runtime behaviour ────────────────────────────────────────────────────
    log_level: str = "INFO"
    environment: str = "development"
    api_prefix: str = "/api"

    # ── LLM Provider Configuration ───────────────────────────────────────────
    # Supported providers: "watsonx", "ollama", "stub"
    # stub = mock responses for offline development
    llm_provider: str = "ollama"  # Default to local Ollama for development
    # Ollama settings (only used if llm_provider = "ollama")
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "granite3.1-dense"

    @property
    def allowed_origins(self) -> list[str]:
        """Return ALLOWED_ORIGINS as a list, splitting on commas."""
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

    @property
    def valid_api_keys(self) -> list[str]:
        """Return VALID_API_KEYS as a list, splitting on commas.

        An empty list means no keys are configured — all requests will be
        rejected in production mode (fail-secure).
        """
        return [k.strip() for k in self.valid_api_keys_raw.split(",") if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def async_database_url(self) -> str:
        """Return asyncpg-compatible URL (postgresql+asyncpg://...)."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()