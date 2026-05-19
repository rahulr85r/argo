from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "argo"
    postgres_user: str = "argo"
    postgres_password: str = "argo"

    chat_model: str = "anthropic/claude-haiku-4-5"
    judge_model: str = "anthropic/claude-haiku-4-5"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
