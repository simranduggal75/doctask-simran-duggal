from datetime import date
from html import escape


def build_daily_report_html(
    *,
    notes: str,
    photo_urls: list[str] | None = None,
) -> str:
    """
    Build a deterministic Daily Field Report shell.

    Important:
    This function does not invent field facts.
    Missing facts remain explicitly marked as 'Not reported'.
    SuperDocs performs the AI-assisted enrichment/editing step later.
    """

    safe_notes = escape(notes.strip())
    photo_urls = photo_urls or []

    photo_section = ""

    if photo_urls:
        photo_items = []

        for index, url in enumerate(photo_urls, start=1):
            photo_items.append(
                f"""
                <div class="photo-page">
                    <img
                        src="{escape(url)}"
                        alt="Site photo {index}"
                        style="max-width:100%;"
                    />
                    <p>
                        <strong>Caption:</strong>
                        Not yet generated
                    </p>
                </div>
                """
            )

        photo_section = "\n".join(photo_items)

    else:
        photo_section = """
        <p><strong>Site photos:</strong> None provided.</p>
        """

    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Daily Field Report</title>

    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 36px;
            color: #222;
        }}

        h1 {{
            text-align: center;
            margin-bottom: 6px;
        }}

        .subtitle {{
            text-align: center;
            color: #666;
            margin-bottom: 28px;
        }}

        h2 {{
            border-bottom: 2px solid #333;
            padding-bottom: 5px;
            margin-top: 28px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
        }}

        th,
        td {{
            border: 1px solid #999;
            padding: 8px;
            text-align: left;
        }}

        th {{
            background: #eee;
        }}

        .source-notes {{
            background: #f5f5f5;
            padding: 14px;
            white-space: pre-wrap;
        }}

        .photo-page {{
            margin-top: 24px;
            page-break-inside: avoid;
        }}
    </style>
</head>

<body>

    <h1>Daily Field Report</h1>

    <div class="subtitle">
        Construction Site Operations Report
    </div>

    <table>
        <tr>
            <th>Project</th>
            <td>Not reported</td>
        </tr>
        <tr>
            <th>Report Date</th>
            <td>{date.today().isoformat()}</td>
        </tr>
        <tr>
            <th>Prepared From</th>
            <td>Superintendent field notes and site photos</td>
        </tr>
    </table>

    <h2>Manpower</h2>

    <table>
        <tr>
            <th>Trade / Crew</th>
            <th>Headcount</th>
            <th>Notes</th>
        </tr>
        <tr>
            <td>Not reported</td>
            <td>Not reported</td>
            <td>Not reported</td>
        </tr>
    </table>

    <h2>Equipment</h2>

    <table>
        <tr>
            <th>Equipment</th>
            <th>Status</th>
            <th>Notes</th>
        </tr>
        <tr>
            <td>Not reported</td>
            <td>Not reported</td>
            <td>Not reported</td>
        </tr>
    </table>

    <h2>Work Performed by Area</h2>

    <table>
        <tr>
            <th>Area</th>
            <th>Work Performed</th>
            <th>Status / Notes</th>
        </tr>
        <tr>
            <td>Not reported</td>
            <td>Not reported</td>
            <td>Not reported</td>
        </tr>
    </table>

    <h2>Weather</h2>
    <p>Not reported.</p>

    <h2>Delays & Causes</h2>

    <table>
        <tr>
            <th>Delay</th>
            <th>Duration</th>
            <th>Cause Category</th>
            <th>Details</th>
        </tr>
        <tr>
            <td>None reported</td>
            <td></td>
            <td></td>
            <td>Not reported.</td>
        </tr>
    </table>

    <h2>Safety Observations</h2>
    <p>Not reported.</p>

    <h2>Source Field Notes</h2>

    <div class="source-notes">
        {safe_notes}
    </div>

    <h2>Site Photos</h2>

    {photo_section}

</body>
</html>
"""