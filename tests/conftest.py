"""Shared test fixtures."""

from pathlib import Path
from shutil import copytree

import pytest


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    """Copy the synthetic vault so every test receives an isolated disposable vault."""
    source = Path(__file__).parent / "fixtures" / "vault"
    destination = tmp_path / "vault"
    copytree(source, destination)
    large_note = destination / "Inbox" / "Large Note.md"
    content = "# Large Note\n\n" + ("A representative long paragraph.\n\n" * 2_000)
    large_note.write_text(content)
    return destination
