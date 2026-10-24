# Catalog data

`public-catalog/` is the intentionally committed public-only snapshot for GitHub
Pages. Export it explicitly with `app/build_static.py --export`; regular builds
read that snapshot offline. Only allowlisted official fields, sample images and
creator portraits are included. Temporary signed image URLs, private photos,
personal records and local exports are excluded. See `docs/deployment.md`.

`catalog/` stores the downloaded public Nikon recipe archive, creator portraits,
sample images and generated JSON/CSV files. It is generated locally and excluded
from Git.

Use the **同步云创** button in the local web UI to download or refresh the public
China catalog. Startup, page loads and photo scans never contact Nikon. There is
no scheduled official sync. An existing archive can be rebuilt without network
access using `python app/collect_nikon.py --offline` followed by
`python app/build_viewer.py`. Online collector CLI calls are disabled.

Refreshes are downloaded into verified `.catalog-stage-*` siblings. JSON, IDs,
images, hashes and paths are validated before promotion. A durable transaction
journal and temporary backup allow failed or interrupted replacements to roll
back. Those transaction paths are excluded from Git as well.

Original recipe titles, creator names and Chinese display text are preserved.
Recipe and creator directory names use their stable official IDs in lowercase.
