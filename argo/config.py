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

    # Identity contract — the upstream auth proxy must set this header on
    # every request, containing the verified end-user identifier. The
    # gateway does NOT validate JWTs; that is the auth proxy's job.
    user_id_header: str = "X-Argo-User-Id"

    # Path to the policy file. Default points at the bundled banking.toml;
    # production deployments will override this to a bank-owned file.
    policy_path: str = ""  # "" → use argo/policy/banking.toml

    # Reference instant for time-windowed policy rules (`recent_payment`).
    #   ""      → wall clock (the production default)
    #   "seed"  → pin to the bundled demo dataset, so it never ages out
    #   ISO-8601 timestamp → an explicit pin
    # See argo/clock.py. Pinning freezes the counterparty graph — demo and
    # test affordance only, never a production posture.
    reference_time: str = ""

    # Pluggable Protocol implementations. Each is "module.path:ClassName".
    # Override via env var to plug in a bank-specific implementation
    # without forking. See ADAPTERS.md for the contracts.
    entitlement_adapter: str = "argo.entitlements:DbDerivedAdapter"
    audit_writer: str = "argo.db.audit:PostgresAuditWriter"
    llm_client: str = "argo.llm:LiteLlmClient"
    verifier: str = "argo.verifier:LlmVerifier"
    transaction_source: str = "argo.db.queries:PostgresTransactionSource"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
