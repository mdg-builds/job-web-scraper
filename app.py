"""Flask app for the Front-End Job Scraper portfolio site."""

from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request

from scraper import DAY_OPTIONS, PRESETS, scrape_jobs

app = Flask(__name__)


@app.route("/favicon.ico")
def favicon():
    return ("", 204)
    """Local HTML board for custom-URL demos and tests."""
    now = datetime.now(timezone.utc)
    sample_jobs = [
        {
            "title": "Senior Front-End Engineer",
            "company": "Northwind Labs",
            "location": "Remote — Americas",
            "url": "https://example.com/jobs/senior-frontend",
            "posted": (now - timedelta(hours=6)).isoformat(),
            "posted_label": "6 hours ago",
            "tags": ["React", "TypeScript", "CSS"],
        },
        {
            "title": "UI Engineer",
            "company": "Cedar Soft",
            "location": "Remote — EU",
            "url": "https://example.com/jobs/ui-engineer",
            "posted": (now - timedelta(days=2)).isoformat(),
            "posted_label": "2 days ago",
            "tags": ["Vue", "Design Systems"],
        },
        {
            "title": "Junior Web Developer",
            "company": "Harbor Studio",
            "location": "Hybrid — NYC",
            "url": "https://example.com/jobs/junior-web",
            "posted": (now - timedelta(days=12)).isoformat(),
            "posted_label": "12 days ago",
            "tags": ["HTML", "JavaScript"],
        },
    ]
    return render_template("demo_board.html", jobs=sample_jobs)


@app.route("/", methods=["GET", "POST"])
def index():
    jobs = []
    error = None
    source_url = None
    filtered_out = 0
    scraped = False

    selected_preset = "remoteok"
    custom_url = ""
    days = 7

    if request.method == "POST":
        selected_preset = (request.form.get("preset") or "").strip()
        custom_url = (request.form.get("custom_url") or "").strip()
        days_raw = request.form.get("days", "7")
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            days = 7

        # Custom URL wins when provided; otherwise use the chosen preset.
        preset = None if custom_url else (selected_preset or None)
        result = scrape_jobs(preset=preset, custom_url=custom_url or None, days=days)
        scraped = True
        jobs = result.jobs
        error = result.error
        source_url = result.source_url
        filtered_out = result.filtered_out
        days = days if days in DAY_OPTIONS else 7

    return render_template(
        "index.html",
        presets=PRESETS,
        day_options=DAY_OPTIONS,
        selected_preset=selected_preset,
        custom_url=custom_url,
        days=days,
        jobs=jobs,
        error=error,
        source_url=source_url,
        filtered_out=filtered_out,
        scraped=scraped,
    )


if __name__ == "__main__":
    app.run(debug=True)
