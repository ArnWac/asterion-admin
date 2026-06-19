from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldInfo:
    name: str
    primary_key: bool
    hidden: bool
    read_only: bool


@dataclass
class AdminModelSchema:
    model_name: str
    fields: list[FieldInfo]
