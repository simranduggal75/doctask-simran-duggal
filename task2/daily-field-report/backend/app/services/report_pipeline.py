import base64
import mimetypes
from pathlib import Path

from app.models.field_report import FieldReportData
from app.services.report_builder import build_daily_report_html
from app.services.report_extractor import extract_field_report
from app.services.superdocs_service import SuperDocsClient


class ReportPipelineError(Exception):
    """Raised when daily field report processing fails."""


def build_image_attachments(
    photo_paths: list[Path],
) -> list[dict]:
    """
    Convert saved report photos into the image attachment format
    expected by SuperDocs chat.
    """

    attachments = []

    for index, photo_path in enumerate(photo_paths, start=1):
        if not photo_path.exists():
            raise ReportPipelineError(
                f"Photo not found: {photo_path}"
            )

        raw_bytes = photo_path.read_bytes()

        mime_type, _ = mimetypes.guess_type(
            photo_path.name
        )

        if not mime_type:
            raise ReportPipelineError(
                f"Unable to determine image type: "
                f"{photo_path.name}"
            )

        attachments.append(
            {
                "id": f"img-{index}",
                "name": photo_path.name,
                "base64Data": base64.b64encode(
                    raw_bytes
                ).decode("ascii"),
                "mimeType": mime_type,
                "size": len(raw_bytes),
            }
        )

    return attachments


async def process_report(
    *,
    report_id: str,
    report_dir: Path,
    notes: str | None,
    photo_filenames: list[str],
) -> dict:
    """
    Process an uploaded field report.

    Pipeline:

    1. Extract structured facts from field notes.
    2. Resolve saved photo files.
    3. Prepare photos for SuperDocs vision.
    4. Ask SuperDocs to analyze the photos.
    5. Build the daily report HTML.
    6. Save the intermediate report.

    SuperDocs document upload, HITL approval,
    and export will be connected in the next stage.
    """

    if not notes:
        raise ReportPipelineError(
            "Field notes are required for report generation."
        )

    try:
        # ---------------------------------------------------------
        # Step 1: Extract structured field data
        # ---------------------------------------------------------

        report: FieldReportData = await extract_field_report(
            report_id=report_id,
            notes=notes,
        )

        # ---------------------------------------------------------
        # Step 2: Resolve saved photo paths
        # ---------------------------------------------------------

        photo_paths: list[Path] = []

        for filename in photo_filenames:
            photo_path = report_dir / filename

            if not photo_path.exists():
                raise ReportPipelineError(
                    f"Saved photo not found: {photo_path}"
                )

            photo_paths.append(photo_path)

        # ---------------------------------------------------------
        # Step 3: Prepare photos for SuperDocs vision
        # ---------------------------------------------------------

        image_attachments = build_image_attachments(
            photo_paths
        )

        # ---------------------------------------------------------
        # Step 4: Analyze photos with SuperDocs
        # ---------------------------------------------------------

        photo_analysis = None

        if image_attachments:
            superdocs = SuperDocsClient()

            photo_analysis = await superdocs.chat(
                session_id=(
                    f"photo-analysis-{report_id}"
                ),
                message="""
You are validating photos for a construction Daily Field Report.

You MUST return ONLY valid JSON.
Do not use Markdown.
Do not add explanations outside the JSON.

Required exact structure:

{
  "photos": [
    {
      "photo_name": "filename",
      "relevant": true,
      "caption": "professional caption",
      "reason": null
    }
  ]
}

For every attached image:

- If the image is a construction site, construction activity,
  construction equipment, construction material, site condition,
  or another clearly relevant field-report image:
    relevant = true
    caption = concise professional visual caption
    reason = null

- If the image is NOT related to construction:
    relevant = false
    caption = null
    reason = "Image is not relevant to a construction field report."

IMPORTANT:
The attached image is NOT automatically a construction-site image.
Judge relevance from the pixels.

For an unrelated image such as wildlife, animals, portraits,
food, scenery, screenshots, etc., relevant MUST be false.

Do not invent a construction context.

Return JSON only.
""",
                image_attachments=image_attachments,
            )

        # ---------------------------------------------------------
        # Step 5: Build the daily report HTML
        # ---------------------------------------------------------

        html = build_daily_report_html(
            report=report,
        )

        # ---------------------------------------------------------
        # Step 6: Save intermediate HTML
        # ---------------------------------------------------------

        html_path = (
            report_dir / "daily_field_report.html"
        )

        html_path.write_text(
            html,
            encoding="utf-8",
        )

        return {
            "report_id": report_id,
            "status": "report_generated",
            "report_data": report.model_dump(
                mode="json"
            ),
            "photo_count": len(photo_paths),
            "photos": [
                str(path)
                for path in photo_paths
            ],
            "photo_analysis": photo_analysis,
            "html_path": str(html_path),
        }

    except ReportPipelineError:
        raise

    except Exception as exc:
        raise ReportPipelineError(
            "Failed to process daily field report."
        ) from exc