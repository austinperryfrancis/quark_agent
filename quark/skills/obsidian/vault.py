"""Narrow filesystem adapter restricted to one Obsidian vault."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

import yaml


class VaultPathError(ValueError):
    pass


class NoteNotFoundError(FileNotFoundError):
    pass


class ObsidianVault:
    def __init__(self, root: str | Path, *, inbox_path: str = "Inbox") -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Obsidian vault does not exist: {self.root}")
        self.inbox_path = inbox_path

    def resolve_note(self, relative_path: str) -> Path:
        candidate_path = PurePosixPath(relative_path)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise VaultPathError("Note path must remain inside the configured vault")
        if candidate_path.suffix.lower() != ".md":
            candidate_path = candidate_path.with_suffix(".md")
        candidate = (self.root / Path(*candidate_path.parts)).resolve()
        if not candidate.is_relative_to(self.root):
            raise VaultPathError("Note path resolves outside the configured vault")
        return candidate

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read_note(self, relative_path: str) -> str:
        path = self.resolve_note(relative_path)
        if not path.is_file():
            raise NoteNotFoundError(f"Obsidian note not found: {relative_path}")
        return path.read_text(encoding="utf-8")

    def latest_inbox_note(self) -> str:
        inbox = (self.root / self.inbox_path).resolve()
        if not inbox.is_relative_to(self.root):
            raise VaultPathError("Inbox path resolves outside the configured vault")
        notes = [path for path in inbox.rglob("*.md") if path.is_file()]
        if not notes:
            raise NoteNotFoundError("No Markdown notes found in the Obsidian inbox")
        latest = max(notes, key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))
        return self.relative(latest)

    def read_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---\n"):
            return {}, content
        marker = content.find("\n---\n", 4)
        if marker == -1:
            raise ValueError("Obsidian note has unterminated YAML frontmatter")
        raw = content[4:marker]
        parsed = yaml.safe_load(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("Obsidian frontmatter must be a YAML mapping")
        return parsed, content[marker + 5 :]

    def apply_tags(self, relative_path: str, tags: list[str]) -> bool:
        path = self.resolve_note(relative_path)
        content = self.read_note(relative_path)
        frontmatter, body = self.read_frontmatter(content)
        if frontmatter.get("tags") == tags:
            return False
        frontmatter["tags"] = tags
        yaml_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        updated = f"---\n{yaml_text}\n---\n{body}"
        self._atomic_write(path, updated)
        return True

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        original_mode = path.stat().st_mode
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, original_mode)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
