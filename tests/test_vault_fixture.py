from pathlib import Path


def test_vault_fixture_is_isolated_and_complete(vault_path: Path) -> None:
    assert (vault_path / "Inbox" / "Meeting Note.md").exists()
    assert (vault_path / "Inbox" / "Empty Note.md").read_text() == ""
    assert (
        "missing closing delimiter"
        in (vault_path / "Inbox" / "Malformed Frontmatter.md").read_text()
    )
    assert (vault_path / "Inbox" / "Large Note.md").stat().st_size > 50_000
    assert (vault_path / "Inbox" / "Partial Frontmatter.md").exists()

    marker = vault_path / "created-by-test"
    marker.write_text("temporary")
    assert marker.exists()
