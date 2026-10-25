from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable value object: safe to cache and share between concurrent agents."""

    model_config = ConfigDict(frozen=True)
