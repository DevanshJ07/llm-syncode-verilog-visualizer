"""
Application-wide configuration loaded from environment variables.
Pydantic Settings validates and coerces all values at startup.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Server
    app_name: str = "SynViz — Verilog Syncode Visualizer API"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS — set to your Next.js dev URL in .env
    cors_origins: list[str] = ["http://localhost:3000"]

    # Model
    # Qwen2.5-Coder-1.5B-Instruct — CPU-compatible, ≈ 3 GB RAM in fp32.
    model_name: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    device: str = "cpu"    # "cpu" | "cuda" | "mps"
    max_new_tokens: int = 120
    default_top_k: int = 20

    # Storage — experiments are stored as JSON files under this directory
    experiments_dir: str = "logs/experiments"

    # Feature flags
    # Syncode grammar-constrained decoding — enabled by default now that
    # syncode 0.4.x is installed.  The service degrades gracefully to raw
    # mode if the package is missing or the grammar fails to compile.
    syncode_enabled: bool = True
    model_loaded: bool = False     # Flipped to True after model warm-up

    # Research mode: when False (default), generation stops immediately when
    # SynCode's parser enters an invalid state instead of falling back to raw
    # unconstrained decoding.  Set to True only for exploratory runs where
    # seeing the raw continuation is acceptable.
    allow_syncode_fallback: bool = False

    # Completion budget (SynCode research mode):
    #   normal display/default limit  = max_new_tokens (typically 120)
    #   if still not parse-valid at that point, continue constrained decoding
    #   for up to completion_extra_tokens more tokens, capped by
    #   absolute_max_tokens.
    #   NORMAL_MAX_TOKENS=120, COMPLETION_EXTRA=80, ABSOLUTE_MAX=200
    completion_extra_tokens: int = 80
    absolute_max_tokens: int = 200


# Singleton — import `settings` everywhere, never instantiate Settings directly.
settings = Settings()
