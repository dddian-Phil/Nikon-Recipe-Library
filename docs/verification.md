# Four-feature update verification

Verified on 2026-10-24 with the existing native Python, HTML, CSS and JavaScript
architecture. No framework or scheduler was added.

| Check | Result |
|---|---|
| Python unittest suite | 39 tests passed |
| JavaScript syntax | `node --check app/app.js` passed |
| Browser interaction checks | 15 checks passed, desktop and 390px mobile |
| Existing archive replay | 144 recipes, 68 creators, 358 recorded responses; no differences falsely reported |
| Source photo and personal record preservation | All 14 baseline file hashes unchanged |
| Browser console | No application errors or warnings during interaction checks |

The isolated backend tests exercise added and changed recipes, changed sample
images, rotating signed URLs, retained withdrawn recipes, invalid/empty response
data, invalid IDs and URLs, corrupt/truncated images, incomplete assets, failed
downloads, duplicate manual starts, directory rename failure, journal write
failure, reload failure and interrupted transaction recovery. They also check
that photo scanning cannot observe uncommitted catalog data during rollback.

Local HTTP tests cover explicit POST-only job creation, cross-site request
rejection, invalid request bodies and catalog path traversal. Creating the
service, reading status, opening a page and building an empty viewer make no
official requests. Online collector CLI calls are disabled; offline archive
rebuilds remain available.

Photo tests cover corrupt files, missing or unsupported Picture Control metadata,
truncated control blocks, missing NEF previews, files still being copied,
permission errors, ambiguous name candidates, cached warnings and preservation of
manual assignments, favorites, backups and source image bytes.

Browser checks cover user-triggered sync progress, disabled repeat clicks,
completion without reloading the page, new badges and filtering, publish-date and
new-first sorting, searchable candidates, empty search results, assigning and
clearing an association, returning to automatic identification, failure messages,
retained catalog data after failure and mobile overflow.

All official responses in tests were synthetic or replayed from the existing
downloaded public archive. Browser mutation requests were intercepted and never
saved to real personal records. **No live official sync was performed by the
agent.** Live connectivity and current upstream availability will be exercised
when the user clicks **同步云创**. A failed attempt leaves the current catalog
available and reports the cause.

Existing `recipes.xlsx` is intentionally left as its original static export;
web sync updates the web catalog, JSON/CSV data, public documents and images.

To repeat the automated checks:

```powershell
python -m unittest discover -s tests -v
node --check app/app.js
```
