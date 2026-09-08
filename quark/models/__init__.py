"""Language-model provider abstractions."""

from quark.config import ModelConfig
from quark.models.ollama import OllamaProvider
from quark.models.provider import ModelProvider


def create_provider(config: ModelConfig) -> ModelProvider:
    """Construct the configured provider without exposing provider details."""
    if config.provider == "ollama":
        return OllamaProvider(config.name, config.endpoint)
    raise ValueError(f"unsupported model provider: {config.provider}")


__all__ = ["ModelProvider", "OllamaProvider", "create_provider"]
