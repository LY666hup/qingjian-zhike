"""Project-local environment loading helpers."""

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_env():
    """Load the optional project .env without replacing exported settings."""
    return load_dotenv(PROJECT_ROOT / ".env", override=False)
