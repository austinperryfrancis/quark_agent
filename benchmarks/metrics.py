"""Metrics for the reviewed Obsidian gold-set format."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast


def score_predictions(
    gold: Sequence[Mapping[str, object]],
    predictions: Mapping[str, Mapping[str, object]],
) -> dict[str, float]:
    """Return tag precision/recall, new-tag rate, and project accuracy."""
    tp = predicted = relevant = new_tags = 0
    project_correct = 0
    for row in gold:
        note_id = str(row["note_id"])
        expected = {str(tag) for tag in cast(list[Any], row.get("acceptable_tags", []))}
        actual_row = predictions.get(note_id, {})
        actual = {str(tag) for tag in cast(list[Any], actual_row.get("tags", []))}
        tp += len(expected & actual)
        predicted += len(actual)
        relevant += len(expected)
        new_tags += len(actual - expected)
        if actual_row.get("project") == row.get("project"):
            project_correct += 1
    count = len(gold)
    return {
        "tag_precision": tp / predicted if predicted else 0.0,
        "tag_recall": tp / relevant if relevant else 0.0,
        "new_tag_rate": new_tags / predicted if predicted else 0.0,
        "project_accuracy": project_correct / count if count else 0.0,
    }
