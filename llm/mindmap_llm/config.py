from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    embedding_model: str = ""
    tavily_api_key: SecretStr = SecretStr("")

    # Generation must not hang forever (US-03: timeout and retry behaviour).
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
