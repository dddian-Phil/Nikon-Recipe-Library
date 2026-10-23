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
