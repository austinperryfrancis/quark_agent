from quark.agent.lifecycle import (
    FeedbackKind,
    Proposal,
    Validation,
    ValidationMode,
    classify_feedback,
    proposal_from_payload,
    proposal_payload,
    review_required,
)


def test_user_mode_requires_a_human_review() -> None:
    proposal = Proposal("skill", "operation", {}, Validation(True), Validation(True))
    assert review_required(proposal)


def test_agent_mode_auto_advances_only_after_both_validators_pass() -> None:
    proposal = Proposal(
        "skill",
        "operation",
        {},
        Validation(True),
        Validation(True),
        ValidationMode.AGENT,
    )
    assert not review_required(proposal)
    rejected = Proposal(
        "skill",
        "operation",
        {},
        Validation(True),
        Validation(False),
        ValidationMode.OFF,
    )
    assert review_required(rejected)


def test_proposal_round_trip_preserves_auditable_context() -> None:
    proposal = Proposal(
        "skill",
        "operation",
        {"value": 1},
        Validation(True, "safe", 1.0),
        Validation(True, "relevant", 0.8),
        request="do the thing",
        text="Preview",
    )
    restored = proposal_from_payload(proposal_payload(proposal))
    assert restored == proposal


def test_feedback_classification_keeps_active_proposals_in_their_subloop() -> None:
    assert classify_feedback("y") is FeedbackKind.APPROVE
    assert classify_feedback("show current proposal") is FeedbackKind.SHOW
    assert classify_feedback("why this folder?") is FeedbackKind.EXPLAIN
    assert classify_feedback("put it in projects") is FeedbackKind.REVISE
