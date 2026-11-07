"""Every tunable value lives here. No magic numbers inline."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (any OpenAI-compatible endpoint: Groq, Ollama, Gemini) ---
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 800
    # How many times to re-ask the model when its JSON fails validation.
    llm_repair_attempts: int = 1

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_poll_timeout_s: int = 30

    # --- Infrastructure ---
    database_url: str = (
        "postgresql+asyncpg://teamcaptain:teamcaptain_pw@localhost:5434/teamcaptain"
    )
    inngest_dev: bool = True
    inngest_event_key: str = ""
    inngest_signing_key: str = ""
    app_port: int = 8000

    # --- Time ---
    timezone: str = "Asia/Kolkata"
    # A deadline with a date but no time ("by Friday") means the end of that day.
    default_deadline_hour: int = 23
    default_deadline_minute: int = 59

    # --- Extraction ---
    # Earlier messages shown to the model, so "ok, I'll do it" can be understood.
    context_messages: int = 4
    extraction_concurrency: int = 2
    # Free tiers are rate-limited; Inngest throttles runs instead of failing them.
    extraction_runs_per_minute: int = 20

    # --- Lifecycle (durable workflow) ---
    # Floats so a live demo can use minutes, e.g. REMINDER_LEAD_HOURS=0.02 (~1 minute).
    confirmation_timeout_hours: float = 24
    reminder_lead_hours: float = 24
    escalation_delay_hours: float = 24
    no_deadline_checkin_days: int = 3
    no_deadline_max_checkins: int = 2
    standup_cron: str = "TZ=Asia/Kolkata 0 21 * * *"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
