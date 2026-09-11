"""Typed, task-local model inference contracts."""

from quark.inference.models import (
    ArgumentDecision,
    ChildSelectionDecision,
    InferenceTaskType,
    InteractionRelationship,
    InteractionRelationshipDecision,
    Message,
    MessageRole,
    ResultValidationDecision,
    ResultValidationOutcome,
    RouteDecision,
    ValidationDecision,
    ValidationOutcome,
)
from quark.inference.ollama import OllamaProvider
from quark.inference.provider import ModelProvider, ModelProviderError
from quark.inference.router import RootRouter, RoutingError

__all__ = [
    "ArgumentDecision",
    "ChildSelectionDecision",
    "InferenceTaskType",
    "InteractionRelationship",
    "InteractionRelationshipDecision",
    "Message",
    "MessageRole",
    "ModelProvider",
    "ModelProviderError",
    "OllamaProvider",
    "ResultValidationDecision",
    "ResultValidationOutcome",
    "RouteDecision",
    "RootRouter",
    "RoutingError",
    "ValidationDecision",
    "ValidationOutcome",
]
