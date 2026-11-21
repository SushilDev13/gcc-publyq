"""Settings from environment / .env. One object, validated once at import."""
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_ignore_empty=True)

    # Vercel's Neon integration injects NEON_DATABASE_URL (pooled); DATABASE_URL wins when both exist
    database_url: str = Field('postgresql://gcc@localhost:5444/gcc',
                              validation_alias=AliasChoices('DATABASE_URL', 'NEON_DATABASE_URL'))
    db_pool_max: int = 4

    # Claude (the brain): official Anthropic SDK
    anthropic_api_key: str = Field('', validation_alias=AliasChoices('ANTHROPIC_API_KEY', 'CLAUDE_API'))
    anthropic_workspace_id: str = ''     # needed only for API keys not scoped to a workspace
    llm_model: str = 'claude-opus-5-5'
    llm_effort: str = 'low'             # voice latency; raise for deeper investigations
    llm_timeout_s: float = 30.0
    llm_max_tool_rounds: int = 6

    # Telegram: the only outbound channel
    bot_token: str = ''
    group_chat_id: str = ''
    # Sign-in: Telegram accounts in the ward group. Admins: comma-separated Telegram user ids (the group's owner
    # is always admin); admin is an account flag, not a post. The bot's username is looked up if unset.
    admin_telegram_ids: str = ''
    telegram_bot_username: str = ''
    # Morning brief (Vercel Cron, 07:00 IST): language and the secret Vercel signs cron calls with
    brief_lang: str = 'ta'
    cron_secret: str = ''

    custom_llm_token: str = ''          # required: unset turns /v1/chat/completions off
    # Origins allowed to call the API from a browser (comma-separated). Same-origin needs no entry.
    cors_origins: str = 'https://gcc.publyq.in'
    mcp_token: str = ''                 # bearer token for /mcp (unset = MCP disabled)

    # Agora ConvoAI + Murf (voice calls)
    agora_app_id: str = ''
    agora_app_certificate: str = ''
    murf_api_key: str = ''
    murf_voice_id: str = 'en-IN-anisha'  # default; en-IN-abhinav selectable
    murf_locale: str = 'en-IN'
    public_base_url: str = ''           # https URL Agora's cloud uses to reach /v1/chat/completions

    media_dir: str = 'Complaints/Images'
    media_base_url: str = ''

    ward_no: int = 173
    log_level: str = 'INFO'

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(',') if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key and self.llm_model)

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        return p if p.is_absolute() else ROOT / p


settings = Settings()
