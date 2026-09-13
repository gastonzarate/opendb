"""Local embeddings and protected pgvector indexes for personal databases."""

from .indexing import process_pending
from .registry import register
from .retrieval import search
from .retrieval import status
from .schema import install

__all__ = ["install", "process_pending", "register", "search", "status"]
