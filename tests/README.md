# Tests

Run from the project root with the existing Python runtime and requirements:

On macOS, run `./setup-mac.command` once, then:

```bash
./.venv/bin/python -B -m unittest discover -s tests -v
```

Mac launcher integration tests use isolated libraries with spaces and Chinese
characters in their paths. They cover start/reuse, stale viewer regeneration,
updates, stopping the owned process, wrong workspaces and failed startups.
The Windows PowerShell launcher tests are skipped on macOS.

On Windows:

```powershell
python -m unittest discover -s tests -v
node --check app/app.js
```

The test suite uses synthetic official responses and generated photo fixtures.
Temporary workspaces live under ignored `local/test-runs/` and are removed after
each test. No real official sync is triggered and no real photos or personal
records are edited.

Coverage includes new/changed recipes, image-only changes, rotating signed URLs,
retained offline recipes, invalid JSON/IDs/images/URLs, transport failure,
duplicate starts, directory rename and journal write failure, reload rollback,
crash recovery, empty installs, local HTTP authorization and path boundaries,
photo parsing diagnostics, partial copies and manual record preservation.
Viewer checks also cover unique control IDs, grouped sync/backup/help actions,
browsing-control placement, visible status regions outside menus, accessible
search/density controls, and embedded styles and escaped catalog payloads.

Touch gestures can be checked in a dedicated Chromium session after starting
the local library (or a static preview with at least two published photos):

```powershell
npx.cmd --yes @playwright/cli -s=nikon-swipe open http://127.0.0.1:8765/ --browser chrome
npx.cmd --yes @playwright/cli -s=nikon-swipe run-code --filename tests/viewer_swipe.browser.js
npx.cmd --yes @playwright/cli -s=nikon-swipe close
```

The browser check sends real touch input for both recipe and photo viewers
in normal detail and focused modes.
It covers direction, queue boundaries, filtered queues, cancelled gestures,
vertical scrolling, multitouch, zoomed panning, short drags, mouse input,
thumbnail scrolling, ordinary taps and immediate button actions after swiping.
It also verifies icon/counter alignment at desktop and mobile widths, no pointer
focus highlight on navigation arrows, and no box or underline during keyboard navigation.
Local photo state is mocked only in this test browser; no records are written.
Screenshots are saved under ignored `output/playwright/`.

JPEG import has an end-to-end check in `photo_import.browser.py`. It starts a
temporary library, uses generated JPEGs and installed Google Chrome, then removes
the library when finished. Install optional test tools into the ignored local
directory (without changing the application environment), then run:

```bash
uv pip install --python .venv/bin/python --target local/browser-test-tools --cache-dir local/uv-cache playwright
PYTHONPATH=local/browser-test-tools ./.venv/bin/python -B tests/photo_import.browser.py
```

This covers native file selection, automatic scanning, exact image/EXIF retention,
duplicates, name collisions, partial failures, JPEG-only restrictions, drag and
drop, active filters, existing detail/like actions, local-only controls, and desktop,
390px and 320px layouts. Results and screenshots go to `output/playwright/`.
Generated JPEGs with MPF embedded previews (opened as MPO) cover byte-for-byte
preservation, EXIF, all embedded frames, rejection of truncated auxiliary images,
HTTP upload, drag-and-drop import and gallery preview generation.

The same browser check verifies the minimal add/delete toolbar follows the gallery,
photo selection, visible-only select-all, keyboard/cancel/Esc, filter and pane changes,
batch deletion and partial-failure retry, persisted batch undo after refresh,
partial restore retry, preserved likes, deleting all photos, sticky mobile controls,
and static-mode restrictions. It also checks matching action icons, nested drag
entry/exit and cancellation, and selection markers below (never over) images at
both gallery densities on desktop and mobile.
Backend trash tests cover grouped copies, concurrent scans, stale confirmations,
changed/missing files, path boundaries, rollback, recovery after restart and restore
name conflicts. All deletion tests use generated photos in temporary libraries.

Photo parameters, separate pane filters, SVG favorite icons and arrow focus styles
can be checked with `run-code --filename tests/viewer_details.browser.js` against
the local library or static preview containing liked photos. Use a dedicated
headless browser session; the check clears only that session’s filter preferences.
