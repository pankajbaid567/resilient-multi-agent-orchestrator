"""Point the app at Amazon Bedrock's OpenAI-compatible endpoint for a live eval run.

Uses a *short-term* Bedrock API key generated locally from your current AWS credentials
(valid up to 12 hours, nothing is created in your AWS project). The key lives only in this
process's environment; it is never printed or written to disk.

Needs the optional packages in requirements-bedrock.txt and AWS credentials for the profile
(for example after `aws login --profile <name>`).
"""

from __future__ import annotations

import os

# In-Region, on-demand models (no cross-Region inference profiles). Prices are the AWS
# Pricing API's standard on-demand rates for ap-south-1 (USD per 1M input / output tokens),
# checked on 2026-11-19; they feed cost reporting through MODEL_PRICES.
DEFAULT_MODELS = {
    "primary": "openai.gpt-oss-120b-1:0",
    "fallback": "qwen.qwen3-32b-v1:0",
    "judge": "openai.gpt-oss-20b-1:0",
}
PRICES_USD_PER_1M = {
    "openai.gpt-oss-120b-1:0": [0.18, 0.71],
    "qwen.qwen3-32b-v1:0": [0.18, 0.71],
    "openai.gpt-oss-20b-1:0": [0.08, 0.35],
}


def configure_bedrock_env(region: str, profile: str | None = None) -> None:
    """Set the environment variables the app reads, before any app module is imported."""
    from aws_bedrock_token_generator import provide_token

    if profile:
        os.environ["AWS_PROFILE"] = profile
    token = provide_token(region=region)
    import json

    os.environ.update({
        "OPEN_SOURCE_API_KEY": token,
        "OPEN_SOURCE_BASE_URL": f"https://bedrock-runtime.{region}.amazonaws.com/openai/v1",
        "OPENAI_API_KEY": "",
        "PRIMARY_MODEL": DEFAULT_MODELS["primary"],
        "FALLBACK_MODEL": DEFAULT_MODELS["fallback"],
        "VALIDATION_MODEL": DEFAULT_MODELS["judge"],
        "MODEL_CHAIN": f"{DEFAULT_MODELS['primary']},{DEFAULT_MODELS['fallback']}",
        "MODEL_PRICES": json.dumps(PRICES_USD_PER_1M),
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://127.0.0.1:1"),
        "CHECKPOINT_URL": "memory",
        "ENABLE_CODE_EXEC": "false",
        "ENABLE_API_CALL": "false",
        "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", ""),
    })
