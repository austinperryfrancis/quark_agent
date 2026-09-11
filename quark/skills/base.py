"""The shared contract for atomic and composite Skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel

from quark.models import ReviewPolicy, SideEffect

if TYPE_CHECKING:
    from quark.inference.models import InferenceTaskType, Message

InferenceOutput = TypeVar("InferenceOutput", bound=BaseModel)


class SkillContext(Protocol):
    """The minimal runtime surface available to a Skill."""

    run_id: str
    call_id: str

    async def call_skill(
        self, skill_name: str, arguments: dict[str, Any]
    ) -> BaseModel:
        """Execute an explicitly allowed child through the runtime."""
        ...

    async def infer(
        self,
        task: InferenceTaskType,
        messages: tuple[Message, ...],
        schema: type[InferenceOutput],
    ) -> InferenceOutput:
        """Request one explicit, typed, task-local inference."""
        ...


class Skill(ABC):
    """One installed capability, whether atomic or composite."""

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_children: tuple[str, ...] = ()
    side_effect: SideEffect = SideEffect.READ_ONLY
    review_policy: ReviewPolicy = ReviewPolicy.OPTIONAL
    result_validation: bool = False
    validator_instructions: str | None = None
    top_level: bool = False
    max_child_calls: int = 50

    @abstractmethod
    async def run(self, ctx: SkillContext, args: BaseModel) -> Any:
        """Perform deterministic work and return output-schema-compatible data."""
        raise NotImplementedError
