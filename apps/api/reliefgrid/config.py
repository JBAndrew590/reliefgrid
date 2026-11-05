from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://reliefgrid:reliefgrid@localhost:5432/reliefgrid"
    migration_database_url: str = "postgresql+psycopg://reliefgrid:reliefgrid@localhost:5432/reliefgrid"
    web_origin: str = "http://localhost:3000"
    secure_cookies: bool = False
    session_hours: int = 12
    demo_seed: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
