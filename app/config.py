from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()  # makes ANTHROPIC_API_KEY from .env visible to the anthropic SDK


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="M2S_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/m2s.db")
    resume_path: Path = Path("data/resume.yaml")
    anthropic_model: str = "claude-opus-5"
    azure_foundry_api_key: str = ""
    azure_foundry_resource: str = ""
    azure_foundry_base_url: str = ""
    azure_foundry_model: str = ""
    product_hunt_token: str = ""
    exa_api_key: str = ""
    hunter_api_key: str = ""
    apollo_api_key: str = ""
    hunter_monthly_limit: int = 25
    # SMTP / sending (Phase 4)
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = ""
    from_name: str = ""
    test_recipient: str = ""
    send_start: str = "09:30"
    send_end: str = "18:30"
    send_timezone: str = "Asia/Kolkata"
    daily_cap: int = 30
    ramp_daily_cap: int = 15
    ramp_days: int = 7
    dkim_selector: str = ""


def get_settings() -> Settings:
    return Settings()
