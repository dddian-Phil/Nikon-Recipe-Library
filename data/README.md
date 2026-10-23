# Catalog data

`catalog/` stores the downloaded public Nikon recipe archive, creator portraits,
sample images and generated JSON/CSV files. It is generated locally and excluded
from Git.

From the project root, run `python app/collect_nikon.py` to download the public
China catalog, then `python app/build_viewer.py` to build the viewer. An existing
archive can be rebuilt without network access using
`python app/collect_nikon.py --offline`.

Original recipe titles, creator names and Chinese display text are preserved.
Recipe and creator directory names use their stable official IDs in lowercase.
