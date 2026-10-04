# Bandipora Farmer Enrollment Portal

Single-portal replacement for the previous "generator + static dashboard" workflow.
You upload one AGRISTACK CSV per day; the portal diffs consecutive snapshots to compute per-day additions. A compact **last-5-days strip** sits on top and the familiar **Additions between** column still responds to the date-range picker.

The baseline through **30 Sept 2026** is frozen in `app.py` exactly as in the earlier generator — uploaded CSVs stack on top.

## Daily workflow

1. Export `Registered_farmer_records_*.csv` from AGRISTACK.
2. Open the portal → click **⬆ Upload daily CSV**.
3. Pick the date (`04.10.2026` or `2026-10-04`) and the file. Submit.
4. The portal saves it as `data/2026-10-04.csv`, parses it, and (if GitHub is configured) commits it to the repo.
5. Reload the dashboard — the new numbers and the last-5-days strip are updated.

If GitHub commit is **not** configured, Render's filesystem is ephemeral — commit `data/YYYY-MM-DD.csv` to the repo manually (GitHub web UI or `git push`) so the snapshot survives the next redeploy.

## Deploy to Render

Prerequisites: a GitHub repo (yours is `peerzadaobaid/agristack-bandipora`).

```bash
# 1. In this project directory, initialize git and push to GitHub
git init
git add .
git commit -m "Switch to upload-driven portal"
git branch -M main
git remote add origin git@github.com:peerzadaobaid/agristack-bandipora.git
git push -u origin main --force   # ⚠ this replaces the previous Flask app
```

On Render:

1. **Dashboard** → New → **Web Service** → connect this repo.
2. Runtime: Python 3.
3. **Build command**: `pip install -r requirements.txt`
4. **Start command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60`
   (Render reads this from `Procfile` automatically, so you can also leave it blank.)
5. **Environment variables** (optional but recommended):
   - `ADMIN_PASSWORD` — any password; required to use the `/upload` page.
   - `GH_TOKEN` — a GitHub Personal Access Token (fine-grained, scoped to this one repo, with **Contents: read & write**). Lets the portal auto-commit uploaded CSVs.
   - `GH_REPO` — `peerzadaobaid/agristack-bandipora`
   - `GH_BRANCH` — `main` (default)

Click **Create Web Service**. First build is 1–2 minutes.

## File layout

```
app.py                        Flask app + all baked-in constants (baseline, targets,
                              camp directors, first-bucket villages, tehsil reference)
Procfile                      gunicorn start command for Render
requirements.txt              Flask + gunicorn
templates/
  dashboard.html              The main portal page (tehsil + camp-director views)
  upload.html                 The /upload form
data/                         Daily CSV snapshots live here, YYYY-MM-DD.csv
  .gitkeep
```

## How the diff works

- `BASELINE_THROUGH_SEP30` in `app.py` holds 40 pre-Oct villages with `[issued, approved]` totals — treated as the state on 30 Sept.
- Each uploaded CSV = full snapshot of all registered farmer records as of that date.
- Day-over-day additions per village = `snapshot(D).village_count − snapshot(D−1).village_count`. For the first upload after baseline, it diffs against baseline totals.
- Villages that aren't yet in any snapshot keep their baseline numbers.
- The last-5-days strip shows the five most recent dates (excluding baseline) with district-wide additions totals.

## Changing baked-in data

The baseline, targets, camp directors, first-bucket set, and tehsil reference are top-of-file constants in `app.py`. Edit, commit, push — Render redeploys.

## Troubleshooting

- **"That CSV didn't parse"** — the file is missing one of the three required columns (tehsil / village / status). The portal recognises both the older AGRISTACK schema (`subDistrictName`, `villageName`, `approvalStatus`) and the newer one (`Sub District Name`, `reports.VillageName`, `Farmer Account Created?`).
- **Numbers didn't change after upload** — check that the file was committed to GitHub (if `GH_TOKEN` isn't set, Render loses the file on next deploy). The dashboard shows the latest snapshot date in its header.
- **A village moved tehsils** or has a new spelling — the generator was tolerant to this; the portal uses the same normalizer. If it still goes missing, add it as a target in `DEFAULT_TARGETS` with target 0.
