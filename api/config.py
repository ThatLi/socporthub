from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    telegram_webapp_url: str = ""
    telegram_webhook_secret: str = ""

    database_url: str = "sqlite:///./dev.db"

    admin_telegram_ids: str = ""
    social_admin_telegram_ids: str = ""
    welfare_admin_telegram_ids: str = ""
    secret_key: str = "dev-secret-key"
    environment: str = "development"

    # Random string appended to the public .ics subscription feed URL so it can't be
    # trivially guessed. Leave blank to serve the feed without a token (fine for local dev).
    calendar_feed_token: str = ""
    resend_api_key: str = ""
    resend_from_email: str = ""
    resend_cc_email: str = "rh.social.i@u.nus.edu"
    welfare_resend_cc_email: str = "rh.social.e@u.nus.edu"
    app_timezone: str = "Asia/Kuala_Lumpur"
    collection_reminder_hour: int = 8
    grading_enabled: bool = False
    grading_remind_admins: bool = True
    ai_reviewer_enabled: bool = False
    ai_reviewer_model: str = "gemini-2.5-flash"
    ai_reviewer_max_chars: int = 60000
    ai_reviewer_timeout_seconds: int = 90
    gemini_api_key: str = ""
    # disabled | live
    google_drive_mode: str = "disabled"
    google_service_account_file: str = ""
    google_service_account_json: str = ""
    google_drive_parent_folder_id: str = ""
    google_drive_social_parent_folder_id: str = ""
    google_drive_welfare_parent_folder_id: str = ""

    # JSON object keyed by committee name. This is intentionally configuration-
    # driven for the first Google Forms test, so adding a form does not require
    # a database migration. Example:
    # {"Sports":{"url":"https://docs.google.com/forms/d/e/.../viewform",
    # "fields":{"request_id":"entry.123"},"sections":["Request","Budget"]}}
    google_form_configs: str = "{}"

    # Telegram id to send finished event/initiative announcements to — in practice
    # a separate relay bot that posts into the announcement channel, not our own
    # bot. 0 means announcing is disabled.
    announcement_telegram_id: int = 0

    @property
    def admin_telegram_id_set(self) -> set[int]:
        return {
            int(raw.strip())
            for raw in self.admin_telegram_ids.split(",")
            if raw.strip()
        }

    @staticmethod
    def _parse_telegram_ids(raw_ids: str) -> set[int]:
        return {int(raw.strip()) for raw in raw_ids.split(",") if raw.strip()}

    @property
    def social_admin_telegram_id_set(self) -> set[int]:
        # Keep the existing ADMIN_TELEGRAM_IDS setting as the Social fallback.
        raw = self.social_admin_telegram_ids or self.admin_telegram_ids
        return self._parse_telegram_ids(raw)

    @property
    def welfare_admin_telegram_id_set(self) -> set[int]:
        return self._parse_telegram_ids(self.welfare_admin_telegram_ids)

    @property
    def all_admin_telegram_id_set(self) -> set[int]:
        return self.social_admin_telegram_id_set | self.welfare_admin_telegram_id_set


@lru_cache
def get_settings() -> Settings:
    return Settings()
