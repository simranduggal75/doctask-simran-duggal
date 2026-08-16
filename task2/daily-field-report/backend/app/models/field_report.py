from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Closed vocabulary so delay causes roll up meaningfully across reports
# instead of "rain" / "Weather" / "weather delay" all being distinct
# strings. Free-form wording from the source notes still lives in
# DelayEntry.description - this field is only for grouping.
CauseCategory = Literal[
    "weather",
    "equipment_failure",
    "material_delay",
    "labor_shortage",
    "permitting_inspection",
    "design_coordination",
    "site_conditions",
    "other",
]


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


_CAUSE_CATEGORY_SYNONYMS: dict[str, CauseCategory] = {
    "weather": "weather",
    "rain": "weather",
    "rainy": "weather",
    "storm": "weather",
    "wind": "weather",
    "snow": "weather",
    "heat": "weather",
    "weather delay": "weather",
    "equipment": "equipment_failure",
    "equipment failure": "equipment_failure",
    "equipment breakdown": "equipment_failure",
    "machine breakdown": "equipment_failure",
    "mechanical failure": "equipment_failure",
    "mechanical issue": "equipment_failure",
    "hydraulic issue": "equipment_failure",
    "material": "material_delay",
    "materials": "material_delay",
    "material delay": "material_delay",
    "supply delay": "material_delay",
    "shipment delay": "material_delay",
    "labor": "labor_shortage",
    "labour": "labor_shortage",
    "labor shortage": "labor_shortage",
    "manpower shortage": "labor_shortage",
    "staffing": "labor_shortage",
    "worker shortage": "labor_shortage",
    "permit": "permitting_inspection",
    "permitting": "permitting_inspection",
    "permit delay": "permitting_inspection",
    "inspection": "permitting_inspection",
    "design": "design_coordination",
    "design change": "design_coordination",
    "design coordination": "design_coordination",
    "coordination": "design_coordination",
    "rfi": "design_coordination",
    "site conditions": "site_conditions",
    "ground conditions": "site_conditions",
    "soil conditions": "site_conditions",
    "site access": "site_conditions",
    "access": "site_conditions",
    "other": "other",
}


class DelayEntry(BaseModel):
    description: str
    duration_hours: float | None = None
    cause_category: CauseCategory | None = None

    @field_validator("cause_category", mode="before")
    @classmethod
    def normalize_cause_category(cls, value):
        """
        Map free-form model output onto the closed vocabulary.

        Exact members of CauseCategory pass through unchanged. Known
        synonyms are mapped. Anything else falls back to "other" rather
        than raising - the original wording is preserved separately in
        DelayEntry.description, and we'd rather admit "other" than
        block extraction or invent a specific category we're not sure
        of.
        """

        if value in (None, ""):
            return None

        if not isinstance(value, str):
            return "other"

        normalized = value.strip().lower()

        if normalized in _CAUSE_CATEGORY_SYNONYMS:
            return _CAUSE_CATEGORY_SYNONYMS[normalized]

        return "other"


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