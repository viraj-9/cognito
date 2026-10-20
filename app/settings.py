"""Environment-backed application settings."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def _load_local_env() -> None:
    """Load simple KEY=value settings from the workspace .env without overriding OS values."""
    env_file=ROOT.parent/".env"
    if not env_file.is_file(): return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key,value=line.split("=",1); key=key.strip(); value=value.strip()
        if value[:1] in {"'",'"'} and value[-1:]==value[:1]: value=value[1:-1]
        if key and key.replace("_","").isalnum(): os.environ.setdefault(key,value)


_load_local_env()


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(os.getenv("LINGUAAI_DATABASE", str(ROOT / "data" / "linguaai.sqlite3")))
    environment: str = os.getenv("LINGUAAI_ENV", "development").lower()
    session_secret: str = os.getenv("LINGUAAI_SESSION_SECRET", "")
    cookie_name: str = "lingua_session"
    session_days: int = 14
    ai_base_url: str = os.getenv("LINGUAAI_AI_BASE_URL", "").rstrip("/")
    ai_api_key: str = os.getenv("LINGUAAI_AI_API_KEY", "")
    ai_model: str = os.getenv("LINGUAAI_AI_MODEL", "")
    ai_timeout_seconds: float = 90.0
    model_path: Path = Path(os.getenv("LINGUAAI_LANGUAGE_MODEL", str(ROOT / "model_artifacts" / "language_model.json")))

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def provider_configured(self) -> bool:
        if not (self.ai_base_url and self.ai_api_key and self.ai_model): return False
        try: parsed=urlsplit(self.ai_base_url)
        except ValueError: return False
        return parsed.scheme in {"https","http"} and bool(parsed.hostname) and (not self.production or parsed.scheme=="https")


settings = Settings()
