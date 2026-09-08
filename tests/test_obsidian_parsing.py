from pathlib import Path

import pytest

from skills.obsidian.models import FrontmatterStatus, LinkStatus, NoteDocument
from skills.obsidian.operations.parse_frontmatter import (
    extract_inline_tags,
    extract_yaml_tags,
    parse_frontmatter,
    parse_note,
)
from skills.obsidian.operations.read_note import read_note


def _document(vault_path: Path, name: str, content: bytes) -> NoteDocument:
    path = vault_path / "Inbox" / name
    path.write_bytes(content)
    return read_note(vault_path, path)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"# No frontmatter\n", FrontmatterStatus.ABSENT),
        (b"---\n---\nBody\n", FrontmatterStatus.EMPTY),
        (b"---\ntitle: Valid\n---\nBody\n", FrontmatterStatus.VALID),
        (b"---\ntitle: Missing close\nBody\n", FrontmatterStatus.MALFORMED),
        (b"---\n- not\n- a mapping\n---\nBody\n", FrontmatterStatus.MALFORMED),
    ],
)
def test_frontmatter_statuses(
    vault_path: Path, content: bytes, expected: FrontmatterStatus
) -> None:
    parsed = parse_frontmatter(_document(vault_path, "Status.md", content))
    assert parsed.status is expected


def test_frontmatter_boundaries_preserve_exact_body_bytes(vault_path: Path) -> None:
    body = b"\r\n# Body\r\n\r\nKeep these bytes.\r\n"
    source = b"---\r\ntitle: Exact\r\ncustom: retained\r\n---\r\n" + body
    parsed = parse_frontmatter(_document(vault_path, "Exact.md", source))

    assert parsed.status is FrontmatterStatus.VALID
    assert parsed.data == {"title": "Exact", "custom": "retained"}
    assert parsed.frontmatter_bytes == b"title: Exact\r\ncustom: retained\r\n"
    assert parsed.body_bytes == body
    assert parsed.original_bytes == source
    assert parsed.closing_end is not None
    assert source[parsed.closing_end :] == body


def test_absent_and_unclosed_frontmatter_do_not_drop_source_bytes(
    vault_path: Path,
) -> None:
    absent = b"Ordinary body\r\n"
    unclosed = b"---\r\ntitle: Unclosed\r\nBody-like content\r\n"

    absent_result = parse_frontmatter(_document(vault_path, "Absent.md", absent))
    unclosed_result = parse_frontmatter(_document(vault_path, "Unclosed.md", unclosed))

    assert absent_result.body_bytes == absent
    assert unclosed_result.body_bytes == unclosed
    assert unclosed_result.error == (
        "opening frontmatter delimiter has no closing delimiter"
    )


def test_yaml_is_loaded_safely(vault_path: Path) -> None:
    source = b"---\nvalue: !!python/object/apply:os.system ['echo unsafe']\n---\nBody\n"
    parsed = parse_frontmatter(_document(vault_path, "Unsafe.md", source))

    assert parsed.status is FrontmatterStatus.MALFORMED
    assert parsed.data == {}
    assert parsed.error is not None


def test_extracts_yaml_and_inline_tags_without_code(vault_path: Path) -> None:
    source = b"""---
tags:
  - '#research'
  - sanctions
  - sanctions
  - 42
---
# Heading
Use #research/project and #meeting. `ignore #inline-code`

```python
#ignore-fence
```
"""
    document = _document(vault_path, "Tags.md", source)
    frontmatter = parse_frontmatter(document)

    assert extract_yaml_tags(frontmatter) == ("research", "sanctions")
    assert extract_inline_tags(frontmatter.body_bytes.decode()) == (
        "research/project",
        "meeting",
    )


def test_resolves_wiki_links_and_reports_ambiguous_and_broken(
    vault_path: Path,
) -> None:
    duplicate = vault_path / "Other" / "Sanctions Plan.md"
    duplicate.parent.mkdir()
    duplicate.write_text("# Another plan\n", encoding="utf-8")
    source = b"""# Links
[[Research/Sanctions Plan#Roadmap|main plan]]
[[../Research/Sanctions Plan#Roadmap]]
[[Sanctions Plan]]
[[Missing Note|missing]]
![[Research/Sanctions Plan]]
[[#Local Heading]]
"""
    document = _document(vault_path, "Links.md", source)
    parsed = parse_note(document, vault_path)

    assert [link.status for link in parsed.links] == [
        LinkStatus.RESOLVED,
        LinkStatus.RESOLVED,
        LinkStatus.AMBIGUOUS,
        LinkStatus.BROKEN,
        LinkStatus.RESOLVED,
        LinkStatus.RESOLVED,
    ]
    assert parsed.links[0].resolved_note_id == "Research/Sanctions Plan.md"
    assert parsed.links[0].heading == "Roadmap"
    assert parsed.links[0].alias == "main plan"
    assert parsed.links[1].resolved_note_id == "Research/Sanctions Plan.md"
    assert parsed.links[2].candidates == (
        "Other/Sanctions Plan.md",
        "Research/Sanctions Plan.md",
    )
    assert parsed.links[3].resolved_note_id is None
    assert parsed.links[4].embedded is True
    assert parsed.links[5].resolved_note_id == "Inbox/Links.md"
