"""``.env`` line normalization shared by the config writer and the dotenv loader.

Lives in its own leaf module because ``hermes_cli.env_loader`` needs it *before* the
dotenv values are published. Importing ``hermes_cli.config`` from there pulled in the
provider/plugin layer first, so plugins that read their defaults from the environment at
import time (mnemosyne's embedding model) snapshotted a half-loaded environment.
"""


def sanitize_env_lines(lines: list) -> list:
    """Normalize .env line endings/whitespace without changing assignment semantics.
    Content after the first ``=`` is opaque value data: a known variable name embedded in a value
    must never be reinterpreted as another assignment, so concatenated lines stay on one line."""
    sanitized: list[str] = []
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.strip()
        # Blank lines and comments are preserved verbatim.
        sanitized.append((raw if not stripped or stripped.startswith("#") else stripped) + "\n")
    return sanitized
