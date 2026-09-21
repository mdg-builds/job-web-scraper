# Frontline

A small Flask app that scrapes public front-end job boards, filters by how recently a role was posted, and shows the matches on one page.

## Features

- Preset boards:
  - [RemoteOK](https://remoteok.com/remote-frontend-jobs) (public JSON feed, front-end tagged roles)
  - [Remotive](https://remotive.com) software-dev API, filtered to front-end titles
  - [We Work Remotely](https://weworkremotely.com/categories/remote-programming-jobs) programming RSS
- Optional custom listing URL (HTML / RSS / known JSON feeds; best-effort)
- Local demo board at `/demo/board` for offline custom-URL tests
- Filter by days posted: 1, 3, 7, 14, or 30
- On-page results with title, company, location, posted time, tags, and link

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

## Notes

- Requests use a clear User-Agent, a timeout, and a response size limit. Do not hammer boards.
- Preset pages are the reliable demo path. Custom URLs depend on recognizable listing markup and may return fewer (or no) results.
- Job boards change their HTML. If a preset breaks, update the parsers in `scraper.py`.
- Be mindful of each site’s terms of use and robots guidance when scraping.

## Project layout

- `app.py` — Flask routes
- `scraper.py` — fetch, parse, normalize, filter
- `templates/` — Jinja2 pages
- `static/style.css` — styles
# job-web-scraper
