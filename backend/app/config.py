import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 120
    cookie_secure: bool = True
    allowed_origins: str = ""

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def check_jwt_secret(self) -> "Settings":
        if len(self.jwt_secret) < 32:
            warnings.warn("JWT_SECRET should be at least 32 characters", stacklevel=2)
        if self.jwt_secret == "change-me-in-prod":
            warnings.warn("JWT_SECRET is set to the default value, change it in production", stacklevel=2)
        return self


settings = Settings()
