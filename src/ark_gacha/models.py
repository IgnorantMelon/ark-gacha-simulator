from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UpGroup(BaseModel):
    rarity: int
    characters: list[str]
    per_character: float


class RuleProfile(BaseModel):
    first_ten: bool = True
    directed_threshold: int | None = None
    directed_target: str | None = None
    target_six: str | None = None
    target_six_threshold: int | None = None
    target_six_mode: Literal["once", "cycle"] | None = None
    complementary_five: bool = False
    target_five_threshold: int | None = None
    extra_gift_at_300: bool = False
    selection_thresholds: list[int] = Field(default_factory=list)


class PoolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    opened_at: int
    closed_at: int
    rule_type: str
    pity_domain: str
    rates: dict[int, float]
    roster: dict[int, list[str]]
    up: list[UpGroup]
    weight_up: dict[str, float] = Field(default_factory=dict)
    rules: RuleProfile
    evidence: str

    def direct_up(self, rarity: int) -> list[str]:
        return list(dict.fromkeys(c for g in self.up if g.rarity == rarity for c in g.characters))


class CatalogSnapshot(BaseModel):
    schema_version: int = 1
    version: str
    fetched_at: str
    sources: dict[str, dict[str, str]]
    operators: dict[str, str]
    pools: list[PoolDefinition]
    excluded: list[dict[str, str]]


class DrawState(BaseModel):
    misses: int = 0
    pulls: int = 0
    first_five_done: bool = False
    directed_misses: int = 0
    directed_done: bool = False
    target_six_misses: int = 0
    target_six_done: bool = False
    first_up_five: str | None = None
    complementary_five_done: bool = False
    up_five_seen: bool = False
    target_five_done: bool = False
    selection_picks: list[str] = Field(default_factory=list)


class DrawResult(BaseModel):
    index: int
    char_id: str
    rarity: int


class DrawMode(StrEnum):
    TEN = "ten"
    WELL = "well"


class UserError(Exception):
    """Safe, user-visible error message."""
