# 🌊 Nicaragua '26 — Surf Trip

A static info site for our July 6–13, 2026 surf trip to El Remanso, Rivas, Nicaragua.

Pages: Home · Surf · Stay · Maps · Photos · Flights · Schedule · Costs · Guide.

## 🏄 Surf report (twice daily)

`surf.html` shows a live Dude-narrated surf report for Playa Remanso — a
morning (dawn patrol) and afternoon session, each with wave height, swell
period/direction, wind, and a firing/fun/rideable rating.

The page reads [`site/data/surf-report.json`](site/data/surf-report.json), which
is refreshed and published by a single self-contained script:

```bash
scripts/update-surf-report.sh
```

That wrapper regenerates the report and, if it changed, commits + pushes so the
site redeploys. Under the hood it runs
[`scripts/generate_surf_report.py`](scripts/generate_surf_report.py), which pulls
a free, no-API-key forecast from [Open-Meteo](https://open-meteo.com/) (waves,
swell, wind, air/water temp, rain, UV) and asks the `claude` CLI to write the
laid-back commentary on top — falling back to a rule-based blurb if `claude`
isn't available. Standard-library Python only, no `pip install` needed.

### Schedule it in cron

The wrapper is cron-hardened: it resolves its own repo path, rebuilds `PATH`
(so `python3`, `git`, `node` and `claude` are all found), logs to
`scripts/surf-report.log`, and only commits when the report actually changed.
So the whole cron line is just:

```cron
# 6:00 AM and 2:00 PM daily — refresh & publish the surf report
0 6,14 * * *  /home/joey/repos/surf-trip/scripts/update-surf-report.sh
```

> **Run it as your own user, not root.** `git push` uses your stored GitHub
> credentials and the Dude narration uses your `~/.claude` login — root has
> neither. Check `scripts/surf-report.log` (gitignored) if anything looks off.

## Running locally

The site is plain HTML/CSS in [`site/`](site/) — open `site/index.html` in a browser,
or serve the folder with the included Docker setup:

```bash
docker compose up -d   # serves on http://localhost:8082
```

## Live mirror

Auto-deployed to GitHub Pages from `site/` on every push to `main`
(see [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml)).

🤙 Dale pues.
