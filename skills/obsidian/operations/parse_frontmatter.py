"""Parse frontmatter, tags, and wiki-links without model reasoning."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from posixpath import normpath
from typing import Any

import yaml

from skills.obsidian.models import (
    FrontmatterDocument,
    FrontmatterStatus,
    LinkStatus,
    NoteDocument,
    NoteReference,
    ParsedNote,
    WikiLink,
)
from skills.obsidian.operations.list_notes import DEFAULT_EXCLUSIONS, list_notes

_INLINE_TAG = re.compile(r"(?<![\w/#])#([\w][\w/-]*)", re.UNICODE)
_WIKI_LINK = re.compile(r"(?P<embedded>!)?\[\[(?P<inner>[^\]\n]+)\]\]")
_INLINE_CODE = re.compile(r"`+[^`]*`+")


def _line_end(line: bytes, start: int) -> int:
    return start + len(line)


def parse_frontmatter(document: NoteDocument) -> FrontmatterDocument:
    """Parse leading YAML while retaining exact bytes and boundary offsets."""
    source = document.original_bytes
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        return FrontmatterDocument(
            status=FrontmatterStatus.ABSENT,
            data={},
            original_bytes=source,
            frontmatter_bytes=None,
            body_bytes=source,
            opening_end=None,
            closing_start=None,
            closing_end=None,
        )

    opening_end = len(lines[0])
    cursor = opening_end
    closing_start: int | None = None
    closing_end: int | None = None
    for line in lines[1:]:
        if line.rstrip(b"\r\n") == b"---":
            closing_start = cursor
            closing_end = _line_end(line, cursor)
            break
        cursor += len(line)

    if closing_start is None or closing_end is None:
        return FrontmatterDocument(
            status=FrontmatterStatus.MALFORMED,
            data={},
            original_bytes=source,
            frontmatter_bytes=source[opening_end:],
            body_bytes=source,
            opening_end=opening_end,
            closing_start=None,
            closing_end=None,
            error="opening frontmatter delimiter has no closing delimiter",
        )

    frontmatter_bytes = source[opening_end:closing_start]
    body_bytes = source[closing_end:]
    try:
        loaded = yaml.safe_load(frontmatter_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        return FrontmatterDocument(
            status=FrontmatterStatus.MALFORMED,
            data={},
            original_bytes=source,
            frontmatter_bytes=frontmatter_bytes,
            body_bytes=body_bytes,
            opening_end=opening_end,
            closing_start=closing_start,
            closing_end=closing_end,
            error=str(error),
        )

    if loaded is None:
        status = FrontmatterStatus.EMPTY
        data: dict[str, Any] = {}
        error_message = None
    elif isinstance(loaded, Mapping):
        status = FrontmatterStatus.VALID
        data = dict(loaded)
        error_message = None
    else:
        status = FrontmatterStatus.MALFORMED
        data = {}
        error_message = "frontmatter root must be a mapping"

    return FrontmatterDocument(
        status=status,
        data=data,
        original_bytes=source,
        frontmatter_bytes=frontmatter_bytes,
        body_bytes=body_bytes,
        opening_end=opening_end,
        closing_start=closing_start,
        closing_end=closing_end,
        error=error_message,
    )


def extract_yaml_tags(frontmatter: FrontmatterDocument) -> tuple[str, ...]:
    """Extract unique tag strings from a valid YAML `tags` field."""
    raw = frontmatter.data.get("tags", [])
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    tags: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip().removeprefix("#")
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _prose_lines(markdown: str) -> Iterable[str]:
    fence: str | None = None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is None:
            yield _INLINE_CODE.sub("", line)


def extract_inline_tags(markdown: str) -> tuple[str, ...]:
    """Extract unique inline tags while ignoring fenced and inline code."""
    tags: list[str] = []
    for line in _prose_lines(markdown):
        for match in _INLINE_TAG.finditer(line):
            tag = match.group(1)
            if tag not in tags:
                tags.append(tag)
    return tuple(tags)


def _target_candidates(
    target: str,
    source: NoteDocument,
    notes: list[NoteReference],
) -> tuple[str, ...]:
    if not target:
        return (source.note_id,)

    normalized = target.strip().replace("\\", "/")
    target_path = PurePosixPath(normalized)
    if target_path.suffix.lower() == ".md":
        target_path = target_path.with_suffix("")

    source_parent = PurePosixPath(source.relative_path.parent.as_posix())
    source_relative = source_parent / target_path
    expected = {
        normpath(target_path.as_posix()).casefold(),
        normpath(source_relative.as_posix()).casefold(),
    }
    matches: list[str] = []
    for note in notes:
        without_suffix = PurePosixPath(note.relative_path.as_posix()).with_suffix("")
        exact_match = without_suffix.as_posix().casefold() in expected
        basename_match = (
            "/" not in normalized
            and without_suffix.name.casefold() == target_path.name.casefold()
        )
        if (exact_match or basename_match) and note.note_id not in matches:
            matches.append(note.note_id)
    return tuple(sorted(matches, key=str.casefold))


def extract_wiki_links(
    markdown: str,
    source: NoteDocument,
    notes: list[NoteReference],
) -> tuple[WikiLink, ...]:
    """Parse wiki-link targets, headings, aliases, and deterministic resolution."""
    links: list[WikiLink] = []
    for match in _WIKI_LINK.finditer(markdown):
        inner = match.group("inner")
        destination, separator, alias = inner.partition("|")
        target, heading_separator, heading = destination.partition("#")
        candidates = _target_candidates(target, source, notes)
        if len(candidates) == 1:
            status = LinkStatus.RESOLVED
            resolved = candidates[0]
        elif candidates:
            status = LinkStatus.AMBIGUOUS
            resolved = None
        else:
            status = LinkStatus.BROKEN
            resolved = None
        links.append(
            WikiLink(
                raw=match.group(0),
                target=target.strip(),
                heading=heading.strip() if heading_separator else None,
                alias=alias.strip() if separator else None,
                embedded=bool(match.group("embedded")),
                status=status,
                resolved_note_id=resolved,
                candidates=candidates,
            )
        )
    return tuple(links)


def parse_note(
    document: NoteDocument,
    vault_path: Path,
    exclusions: Iterable[str] = DEFAULT_EXCLUSIONS,
) -> ParsedNote:
    """Parse all deterministic Markdown metadata from a note snapshot."""
    frontmatter = parse_frontmatter(document)
    body = frontmatter.body_bytes.decode("utf-8")
    notes = list_notes(vault_path, exclusions)
    return ParsedNote(
        document=document,
        frontmatter=frontmatter,
        yaml_tags=extract_yaml_tags(frontmatter),
        inline_tags=extract_inline_tags(body),
        links=extract_wiki_links(body, document, notes),
    )
