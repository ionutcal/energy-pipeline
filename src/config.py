"""Configurare centralizata, citita din variabile de mediu."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    # ENTSO-E
    api_key: str = os.getenv("ENTSOE_API_KEY", "")

    # Tarile monitorizate (coduri ISO). RO = Romania.
    countries: list[str] = field(default_factory=lambda: _env_list("COUNTRIES", "RO"))

    # Cate zile in urma se face backfill la prima rulare (cand tabela e goala)
    backfill_days: int = int(os.getenv("BACKFILL_DAYS", "30"))

    # Baza de date
    db_user: str = os.getenv("DB_USER", "postgres")
    db_pass: str = os.getenv("DB_PASS", "")
    db_name: str = os.getenv("DB_NAME", "energy")
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: str = os.getenv("DB_PORT", "5432")

    # Suprascrie complet URL-ul bazei de date (util pentru teste cu SQLite)
    database_url_override: str = os.getenv("DATABASE_URL", "")

    # Retry la apelurile de retea
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    retry_backoff_seconds: float = float(os.getenv("RETRY_BACKOFF_SECONDS", "2"))

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_pass}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


config = Config()
