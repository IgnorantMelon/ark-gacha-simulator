from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARK_", env_file=".env", extra="ignore")

    admin_qq: set[str] = Field(default_factory=set)
    allowed_groups: set[str] = Field(default_factory=set)
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    onebot_token: str = ""
    data_dir: Path = Path("data")
    font_path: str = ""

    @property
    def database(self) -> Path:
        return self.data_dir / "bot.sqlite3"
