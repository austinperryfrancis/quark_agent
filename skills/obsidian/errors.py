"""Domain errors for deterministic Obsidian operations."""


class ObsidianError(Exception):
    """Base error for Obsidian skill failures."""


class VaultNotFoundError(ObsidianError):
    """The configured vault does not exist."""


class InvalidVaultError(ObsidianError):
    """The configured vault path is not a readable directory."""


class NoteNotFoundError(ObsidianError):
    """A requested note does not exist."""


class NoteOutsideVaultError(ObsidianError):
    """A requested note resolves outside the configured vault."""


class NoteEncodingError(ObsidianError):
    """A note is not valid UTF-8 text."""


class NoteReadError(ObsidianError):
    """A note could not be read safely."""


class NoteChangedDuringReadError(NoteReadError):
    """A note changed while Quark was reading it."""


class InvalidNotePatchError(ObsidianError):
    """A proposed note patch failed deterministic validation."""


class NoteChangedBeforeWriteError(ObsidianError):
    """A note no longer matches the snapshot used to build its patch."""


class NoteWriteError(ObsidianError):
    """A validated note patch could not be applied safely."""


class NoteVerificationError(NoteWriteError):
    """A written note did not match the expected result after replacement."""
