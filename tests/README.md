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
