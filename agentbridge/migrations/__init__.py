"""Packaged PostgreSQL migrations for payment lifecycle storage."""

from pathlib import Path

MIGRATION_DIR = Path(__file__).resolve().parent

__all__ = ["MIGRATION_DIR"]
