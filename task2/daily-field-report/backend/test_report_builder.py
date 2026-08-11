from app.services.report_builder import build_daily_report_html


def main():
    html = build_daily_report_html(
        notes=(
            "18 workers were onsite today. "
            "Concrete work completed in Zone A. "
            "Excavator stopped for two hours due to a mechanical issue. "
            "Light rain occurred after 3 PM."
        ),
    )

    with open(
        "sample_daily_report.html",
        "w",
        encoding="utf-8",
    ) as file:
        file.write(html)

    print("Daily report HTML generated.")


if __name__ == "__main__":
    main()