# Tests

Run from the project root with the existing Python runtime and requirements:

```powershell
python -m unittest discover -s tests -v
node --check app/app.js
```

The dependency-free Pages boundary tests can also run on their own:

```powershell
python -B -m unittest discover -s tests -p test_static_build.py -v
python -B app/build_static.py
```

These verify the public snapshot and output allowlists, field projection,
credential/signed-URL rejection, path boundaries, image hashes, CSV escaping and
offline builds. They never read real personal records or source photos.

The test suite uses synthetic official responses and generated photo fixtures.
Temporary workspaces live under ignored `local/test-runs/` and are removed after
each test. No real official sync is triggered and no real photos or personal
records are edited.

Coverage includes new/changed recipes, image-only changes, rotating signed URLs,
retained offline recipes, invalid JSON/IDs/images/URLs, transport failure,
duplicate starts, directory rename and journal write failure, reload rollback,
crash recovery, empty installs, local HTTP authorization and path boundaries,
photo parsing diagnostics, partial copies and manual record preservation.
Public gallery checks also cover unique status IDs after static conversion,
local-only action markers, grouped browsing controls, dark embedded styles,
relative public links, and escaped catalog payloads.

For touch regression checks, build and serve `dist/` locally, then run in a
dedicated Chromium session (replace the URL with the preview's port):

```powershell
npx.cmd --yes @playwright/cli -s=nikon-swipe open http://127.0.0.1:8770/ --browser chrome
npx.cmd --yes @playwright/cli -s=nikon-swipe run-code --filename tests/viewer_swipe.browser.js
npx.cmd --yes @playwright/cli -s=nikon-swipe close
```

This sends real touch input for recipes and photos, covering directions,
queue boundaries, filtered queues, cancellation, vertical scroll, multitouch,
zoomed panning, short drags, thumbnail scroll, taps and immediate button actions.
It never changes published or personal records. Screenshots are saved under
ignored `output/playwright/`. Physical iPhone Safari behavior still needs device QA.
