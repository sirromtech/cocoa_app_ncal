# NCAL Cocoa Processing App

An offline-first Django app that NCAL (Numbo Cooperative Association Ltd) cocoa farmers in Wewak / East Sepik use to record fermentation and drying. Depot staff use the same app to run the moisture and cut tests. It follows the *Mobile Application Design Brief – Cocoa Processing* and the LCCP-Biscochoc work instruction.

## Who uses what

| User | Device | Screens | Network |
|---|---|---|---|
| **Farmer** | Phone (installed as an app) | `/app/`: new batch, step-by-step fermentation and drying, photos, QR code, own results | **Works offline.** Records sync when network comes back |
| **Quality assessor** | Phone or desktop | `/depot/`: scan the QR code, receive bags, moisture and cut test per bag | Needs network |
| **Manager / admin** | Desktop | `/dashboard/`, `/batches/`, `/reports/`, `/farmers/`, `/admin/` | Needs network |

## Quick start

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations core
python manage.py migrate
python manage.py seed_demo        # demo coops, clusters, logins
python manage.py runserver 0.0.0.0:8000
```

Demo logins:
- `admin` / `admin123` (superuser)
- `assessor` / `assessor123` (depot)
- Farmers `DEMO001`, `DEMO002` and `DEMO003`, all with PIN `1234`

> **The offline mode needs HTTPS.** Browsers only allow service workers and `crypto.randomUUID` on `https://` or `http://localhost`. To test on a real phone, deploy behind HTTPS, or use a tunnel such as `ngrok http 8000`. Then set `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain`.

## How offline sync works

1. The farmer logs in once with network. The **service worker** (`/sw.js`) caches the app shell, CSS, JS and icons.
2. Every screen in `/app/` is drawn from **IndexedDB** on the phone (`static/js/db.js`), not from the server. Nothing needs network.
3. Each batch, step and photo gets a **UUID created on the phone** and is saved with `_dirty=1`.
4. `static/js/sync.js` pushes the queue when any of these happens:
   - the `online` event fires
   - the app is opened or brought back to the front
   - every 60 seconds
   - Background Sync triggers (Android Chrome)

   The order is: batches, then steps (`POST /api/sync/`), then photos one at a time (`POST /api/photos/`). After that it pulls the server copy (`GET /api/bootstrap/`), which includes depot results.
5. Pushes are **idempotent**: sending the same UUID twice does nothing. Conflicts are **last-write-wins** on `client_updated_at`. A record is only marked clean if it wasn't edited again during upload (`_rev` check).
6. Once the depot receives a batch, it is **locked** and edits from the phone are ignored. Local photo copies are then deleted to free storage.
7. Photos are shrunk on the phone to about 1280 px JPEG (roughly 150–250 KB) before saving, so they upload on weak signal.
8. GPS is recorded with each step when available. It works without network.

## Process steps (from the client work instruction)

```
1st fermentation 48h → turn → 2nd 48h → turn → 3rd 24h → turn → 4th 24h
→ Solar day 1 → 2 → 3          (or Fermentry day 1 → Solar day 1 → 2)
→ Packing into jute bags → Depot: moisture + cut test
```
The steps are defined in `core/steps.py`. Finishing a step starts the next one automatically, and the end weight carries forward as the next start weight. Every step requires a time and a photo. Fermentation, drying and packing also require a weight.

## Brief requirements → where they are

| Requirement | Implementation |
|---|---|
| Farmer ID: member no., cluster, cooperative | `Farmer` → `Cluster` → `Cooperative` |
| Source of wet beans | `Batch.bean_source`: own garden, purchased, or cluster contribution, plus `source_detail` |
| Fermentation by individual, cluster or society | `Batch.fermented_by` |
| Date and time of each step | `ProcessStep.started_at` / `ended_at` |
| Weight at start and finish of each step | `ProcessStep.weight_start_kg` / `weight_end_kg` |
| Photo evidence for each step | `StepPhoto` (camera capture, stored offline, uploaded later) |
| QR code per batch | `/batches/<code>/qr.svg` (ReportLab). Printable bag labels at `/batches/<code>/label/?bags=1`. The QR opens the public `/verify/<code>/` traceability page |
| Summary reports for farmer, cluster and cooperative | `/reports/` with yield, approval %, premium %, average moisture and cut, process compliance, plus CSV export |
| Moisture 6–8% = approved | `DepotTest.moisture_ok` |
| Cut test ≤ 14% = premium | `DepotTest.premium` |

Thresholds live in `settings.COCOA` and can be changed without code changes.

## Assumptions to confirm with NCAL

- **Cut test:** the "14%" is read as the **total defective beans** (purple + slaty + mouldy + insect-damaged + germinated) ÷ beans cut. Change this in `DepotTest.save()` if LCCP measures it differently.
- **Fermentry branch:** the brief's diagram shows "Solar drier first day" twice. It is treated as Fermentry day 1 → Solar day 1 → Solar day 2.
- **Batch acceptance:** a batch becomes *Accepted* when every tested bag passes moisture. If any bag fails, it becomes *Not accepted – re-dry*. Results are also shown per bag.
- **Fermentation timing:** ±6 h on each fermentation stage counts as compliant (`FERMENT_TOLERANCE_HOURS`).

## Production notes

- Set `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`.
- For PostgreSQL, set `DB_ENGINE=postgres` and add `psycopg[binary]`.
- Run `python manage.py collectstatic`. Serve `/static/` and `/media/` from nginx, WhiteNoise, or cloud storage for photos. `/sw.js` must be served by Django at the site root.
- Bump `COCOA["APP_VERSION"]` whenever you change CSS or JS so phones refresh their cache.
- Farmers stay logged in for 90 days (`SESSION_COOKIE_AGE`). If a session expires offline, records stay on the phone and the app asks them to log in again to send.

## Project layout

```
cocoa/            settings, urls
core/models.py    Cooperative, Cluster, Farmer, Batch, ProcessStep, StepPhoto, Bag, DepotTest
core/steps.py     processing steps from the work instruction
core/api.py       offline sync API (bootstrap / sync / photos)
core/views.py     staff screens, depot, reports, QR, verify
core/reports.py   farmer / cluster / cooperative summaries
templates/app/shell.html   farmer PWA shell
templates/sw.js            service worker
static/js/db.js | sync.js | app.js   IndexedDB, sync engine, farmer UI
static/css/app.css         shared mobile + desktop styles
```
