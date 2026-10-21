from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_reasoning_effort: str = ""  # e.g. "none" for reasoning models that refuse tools otherwise; empty = not sent (auto-retried once if the model demands it)
    liara_api_token: str = ""
    liara_team_id: str = ""  # set when your apps live in a team (see `liara-agent whoami`)
    max_agent_steps: int = 25
    language: str = "en"  # language of text the model writes for the developer (notes, questions): en | fa


def get_settings() -> Settings:
    return Settings()
