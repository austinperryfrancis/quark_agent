"""Safe related-note link validation and managed-section rendering."""

from __future__ import annotations

from collections.abc import Iterable


def validate_related_ids(
    selected: Iterable[str], indexed_ids: Iterable[str], target_id: str
) -> tuple[str, ...]:
    """Keep only real, non-target note identifiers; reject hallucinations."""
    valid = set(indexed_ids)
    result: list[str] = []
    for note_id in selected:
        if note_id == target_id:
            continue
        if note_id not in valid:
            raise ValueError(f"unknown related note identifier: {note_id}")
        if note_id not in result:
            result.append(note_id)
    return tuple(result)


def render_related_section(note_ids: Iterable[str], existing: str = "") -> str:
    """Render a deterministic managed section without duplicating links."""
    links = []
    for note_id in note_ids:
        link = f"- [[{note_id.removesuffix('.md')}]]"
        if link not in links:
            links.append(link)
    section = "## Related Notes\n\n" + ("\n".join(links) if links else "_None yet._")
    marker = "<!-- quark:related-notes -->"
    managed = f"{marker}\n{section}\n{marker}"
    if marker in existing:
        start = existing.index(marker)
        end = existing.index(marker, start + len(marker)) + len(marker)
        return existing[:start] + managed + existing[end:]
    return f"{existing.rstrip()}\n\n{managed}" if existing.strip() else managed
