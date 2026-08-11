from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()  # makes ANTHROPIC_API_KEY from .env visible to the anthropic SDK


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="M2S_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/m2s.db")
    anthropic_model: str = "claude-opus-5"
    product_hunt_token: str = ""
    exa_api_key: str = ""
    hunter_api_key: str = ""
    apollo_api_key: str = ""
    hunter_monthly_limit: int = 25


def get_settings() -> Settings:
    return Settings()
