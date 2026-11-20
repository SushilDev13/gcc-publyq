"""Settings from environment / .env. One object, validated once at import."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')

    database_url: str = 'postgresql://gcc@localhost:5444/gcc'
    db_pool_max: int = 4

    llm_base_url: str = ''
    llm_model: str = ''
    llm_api_key: str = ''
    llm_timeout_s: float = 20.0
    llm_max_tool_rounds: int = 4

    custom_llm_token: str = ''

    media_dir: str = 'Complaints/Images'
    media_base_url: str = ''

    ward_no: int = 173
    log_level: str = 'INFO'

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        return p if p.is_absolute() else ROOT / p


settings = Settings()
