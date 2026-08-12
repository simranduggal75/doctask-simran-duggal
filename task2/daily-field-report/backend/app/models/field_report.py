from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator

class ManpowerEntry(BaseModel):
    trade: str
    headcount: int | None = None
    notes: str | None = None


class EquipmentEntry(BaseModel):
    equipment: str
    status: str | None = None
    notes: str | None = None


class WorkAreaEntry(BaseModel):
    area: str
    work_performed: str
    status: str | None = None


class DelayEntry(BaseModel):
    description: str
    duration_hours: float | None = None
    cause_category: str | None = None


class FieldReportData(BaseModel):
    project: str | None = None
    report_date: date | None = None
    total_headcount: int | None = None
    
    @field_validator("report_date", mode="before")
    @classmethod
    def normalize_report_date(cls, value):
        if value in (None, ""):
            return None

        if isinstance(value, date):
            return value

        if isinstance(value, str):
            value = value.strip()

            formats = [
                "%Y-%m-%d",
                "%B %d, %Y",
                "%b %d, %Y",
                "%d %B %Y",
                "%d %b %Y",
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(
                        value,
                        fmt,
                    ).date()
                except ValueError:
                    continue

        return value

    manpower: list[ManpowerEntry] = Field(default_factory=list)
    equipment: list[EquipmentEntry] = Field(default_factory=list)
    work_by_area: list[WorkAreaEntry] = Field(default_factory=list)

    weather: str | None = None

    delays: list[DelayEntry] = Field(default_factory=list)

    safety_observations: list[str] = Field(
        default_factory=list
    )

    source_notes: str