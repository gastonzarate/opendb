"""Protected semantic annotations and PostgreSQL-backed schema discovery."""

from .install import CatalogError
from .install import install
from .introspection import describe

__all__ = ["CatalogError", "describe", "install"]
