# Tests

Run from the project root with the existing Python runtime and requirements:

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
focus highlight on navigation arrows, and a quiet keyboard focus indicator.
Local photo state is mocked only in this test browser; no records are written.
Screenshots are saved under ignored `output/playwright/`.
