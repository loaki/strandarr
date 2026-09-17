from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "strandarr"
    postgres_password: str = "strandarr"
    postgres_db: str = "strandarr"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    gfw_api_token: str = ""
    aisstream_api_key: str = ""

    forecast_hours: int = 72

    grid_step_deg: float = 0.25

    max_distance_to_coast_km: float = 200.0

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
