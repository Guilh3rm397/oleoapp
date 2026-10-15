from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

Period = Literal["30d", "6m", "12m", "all"]
ReminderStatus = Literal["none", "ok", "soon", "overdue"]

VOLUME_MIN_ML = 1
VOLUME_MAX_ML = 10_000


class DisposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_ml: int = Field(json_schema_extra={"minimum": VOLUME_MIN_ML, "maximum": VOLUME_MAX_ML})
    point_id: str | None = None

    @field_validator("volume_ml")
    @classmethod
    def _check_volume(cls, value: int) -> int:
        if not VOLUME_MIN_ML <= value <= VOLUME_MAX_ML:
            raise PydanticCustomError("volume_range", "Informe de 1 mL a 10 L por registro.")
        return value


class DisposalPointOut(BaseModel):
    id: str
    name: str


class DisposalOut(BaseModel):
    id: str
    volume_ml: int
    point: DisposalPointOut | None = None
    created_at: datetime


class DisposalListOut(BaseModel):
    items: list[DisposalOut]


class ImpactFactorsOut(BaseModel):
    water_l_per_l_oluc: int
    co2e_kg_per_l_oluc: float


class ImpactSourcesOut(BaseModel):
    water_l_per_l_oluc: str
    co2e_kg_per_l_oluc: str


class ImpactOut(BaseModel):
    period: Period
    total_ml: int
    count: int
    water_l: int
    co2e_kg: float
    factors_version: str
    factors: ImpactFactorsOut
    sources: ImpactSourcesOut


class ReminderOut(BaseModel):
    status: ReminderStatus
    last_disposal_at: datetime | None = None
    days_since: int | None = None
    days_left: int | None = None
    reminder_interval_days: int
