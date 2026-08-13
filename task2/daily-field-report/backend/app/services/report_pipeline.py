import base64
import json
import mimetypes
import re
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


def build_photo_data_urls(
    photo_paths: list[Path],
) -> list[str]:
    """
    Convert saved photos into data URLs so the generated HTML
    contains the actual images and remains self-contained.
    """

    urls = []

    for photo_path in photo_paths:
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

        encoded = base64.b64encode(
            raw_bytes
        ).decode("ascii")

        urls.append(
            f"data:{mime_type};base64,{encoded}"
        )

    return urls


def _extract_json_photo_results(
    response_text: str,
) -> list[dict]:
    """
    Try to recover structured photo results if SuperDocs
    happens to return valid JSON.

    Returns an empty list when the response is not JSON.
    """

    text = response_text.strip()

    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            photos = parsed.get("photos")

            if isinstance(photos, list):
                return photos

    except json.JSONDecodeError:
        pass

    # SuperDocs may wrap JSON in a Markdown code block.
    match = re.search(
        r"```(?:json)?\s*(\{.*\})\s*```",
        text,
        flags=re.DOTALL,
    )

    if match:
        try:
            parsed = json.loads(
                match.group(1)
            )

            if isinstance(parsed, dict):
                photos = parsed.get("photos")

                if isinstance(photos, list):
                    return photos

        except json.JSONDecodeError:
            pass

    return []


def extract_photo_captions(
    *,
    response_text: str,
    photo_paths: list[Path],
) -> list[str]:
    """
    Convert SuperDocs' vision response into safe captions.

    SuperDocs does not reliably return the requested JSON format,
    so this function supports both structured JSON and prose.

    Unsupported claims about project impact, safety, disruption,
    or location are intentionally not copied into the report.
    """

    if not photo_paths:
        return []

    structured_results = _extract_json_photo_results(
        response_text
    )

    captions: list[str] = []

    for index, photo_path in enumerate(photo_paths):
        caption = None
        relevant = True

        if index < len(structured_results):
            result = structured_results[index]

            if isinstance(result, dict):
                relevant = result.get(
                    "relevant",
                    True,
                )

                candidate = result.get(
                    "caption"
                )

                if isinstance(candidate, str):
                    caption = candidate.strip()

        # If the model explicitly identifies the image as
        # unrelated to construction, use a safe deterministic
        # caption instead of copying speculative AI claims.
        lower_response = response_text.lower()

        unrelated_markers = (
            "not a construction site",
            "not related to construction",
            "not a construction",
            "wildlife",
            "primate",
            "animal",
            "portrait",
        )

        if any(
            marker in lower_response
            for marker in unrelated_markers
        ):
            relevant = False

        if not relevant:
            caption = (
                "Image does not depict a construction "
                "site activity."
            )

        if not caption:
            # Try to recover the explicitly labelled caption
            # from SuperDocs' prose response.
            match = re.search(
                r"Daily Field Report Caption:\s*"
                r'[\"“](.*?)[\"”]',
                response_text,
                flags=re.IGNORECASE | re.DOTALL,
            )

            if match:
                candidate = match.group(1).strip()

                if candidate:
                    caption = candidate

        if not caption:
            caption = (
                f"Site photo {index + 1}; "
                "visual details available from the attached image."
            )

        captions.append(caption)

    return captions


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
    4. Generate photo captions.
    5. Build the daily report HTML with photos and captions.
    6. Save the intermediate report.

    SuperDocs document upload, HITL approval,
    and export are connected in the next stage.
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
        photo_captions: list[str] = []

        if image_attachments:
            superdocs = SuperDocsClient()

            photo_analysis = await superdocs.chat(
                session_id=(
                    f"photo-analysis-{report_id}"
                ),
                message="""
You are analyzing photos for a construction Daily Field Report.

For each attached image, describe ONLY what is visibly present.

If the image is relevant to construction, provide a concise,
professional field-report caption.

If the image is unrelated to construction, clearly state that
it is not a construction-site image.

Do not invent:
- project activities
- locations
- safety conditions
- environmental impact
- project impact
- causes
- events that cannot be visually established

A caption must describe visible evidence only.
""",
                image_attachments=image_attachments,
            )

            response_text = str(
                photo_analysis.get(
                    "response",
                    ""
                )
            )

            photo_captions = extract_photo_captions(
                response_text=response_text,
                photo_paths=photo_paths,
            )

        # ---------------------------------------------------------
        # Step 5: Convert photos to self-contained HTML URLs
        # ---------------------------------------------------------

        photo_urls = build_photo_data_urls(
            photo_paths
        )

        # ---------------------------------------------------------
        # Step 6: Build daily report HTML
        # ---------------------------------------------------------

        html = build_daily_report_html(
            report=report,
            photo_urls=photo_urls,
            photo_captions=photo_captions,
        )

        # ---------------------------------------------------------
        # Step 7: Save intermediate HTML
        # ---------------------------------------------------------

        html_path = (
            report_dir
            / "daily_field_report.html"
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
            "photo_captions": photo_captions,
            "photo_analysis": photo_analysis,
            "html_path": str(html_path),
        }

    except ReportPipelineError:
        raise

    except Exception as exc:
        raise ReportPipelineError(
            "Failed to process daily field report."
        ) from exc