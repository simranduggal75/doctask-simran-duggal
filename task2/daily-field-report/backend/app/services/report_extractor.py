import json

from app.services.superdocs_service import SuperDocsClient
from app.models.field_report import FieldReportData


EXTRACTION_INSTRUCTION = """
Extract factual construction-site information from the field notes.

Return ONLY valid JSON matching this structure:

{
  "project": null,
  "report_date": null,
  "total_headcount": null,
  "manpower": [],
  "equipment": [],
  "work_by_area": [],
  "weather": null,
  "delays": [],
  "safety_observations": [],
  "source_notes": ""
}

Rules:

1. Use ONLY facts explicitly supported by the source notes.
2. Never invent missing information.
3. Unknown scalar values must be null.
4. Unknown lists must remain empty.
5. Preserve the wording of factual observations where useful.
6. For manpower entries:
   {
     "trade": "...",
     "headcount": number or null,
     "notes": "..." or null
   }
7. For equipment entries:
   {
     "equipment": "...",
     "status": "..." or null,
     "notes": "..." or null
   }
8. For work areas:
   {
     "area": "...",
     "work_performed": "...",
     "status": "..." or null
   }
9. For delays:
   {
     "description": "...",
     "duration_hours": number or null,
     "cause_category": "..." or null
   }
10. Do not infer a safety observation just because one is normally
    expected in a construction report.
11. Keep source_notes equal to the original field notes.
12. For total_headcount, use an explicitly stated total if present.
    Do not calculate a total from trade entries unless the source explicitly
    states that total.

Return JSON only.
"""


class ReportExtractionError(Exception):
    """Raised when field report extraction fails."""


async def extract_field_report(
    *,
    report_id: str,
    notes: str,
) -> FieldReportData:
    client = SuperDocsClient()

    result = await client.chat(
    session_id=f"field-report-extraction-{report_id}",
    message=(
        EXTRACTION_INSTRUCTION
        + "\n\nSOURCE FIELD NOTES:\n"
        + notes
    ),
)

    response_text = result.get("response")

    if not response_text:
        raise ReportExtractionError(
            "SuperDocs returned an empty extraction response."
        )

    try:
        extracted = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ReportExtractionError(
            "SuperDocs did not return valid JSON."
        ) from exc

    try:
        return FieldReportData.model_validate(extracted)
    except Exception as exc:
        raise ReportExtractionError(
            "Extracted field report failed validation."
        ) from exc