"""Restore the paired v1 source trees, backing up both current trees first."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

VERSIONS = (
    ('main', '.', 'v1', '81901f7c8e7ca0eb9782676d63e3810361344d34'),
    ('web', 'local/worktrees/static-deploy', 'v1-web', '39c85361bf9a58dbe611c26023a7f3ebe381b61d'),
)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def prepare(root):
    plans = []
    for label, relative, tag, expected in VERSIONS:
        tree = (root / relative).resolve()
        if not tree.is_relative_to(root):
            raise ValueError('Restore directory is outside the project')
        actual = git(tree, 'rev-parse', tag + '^{commit}').decode().strip()
        if actual != expected:
            raise ValueError('The saved ' + tag + ' tag does not match the recorded v1')
        archive = git(tree, 'archive', '--format=zip', tag)
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            if source.testzip():
                raise ValueError('The v1 archive failed verification')
            for item in source.infolist():
                if item.is_dir():
                    continue
                path = PurePosixPath(item.filename)
                if item.filename == 'photos/.gitkeep':
                    continue
                if path.is_absolute() or '..' in path.parts or path.parts[0] in {'.git', 'local', 'photos'}:
                    raise ValueError('Unexpected path in v1 archive')
                target = (tree / item.filename).resolve()
                if not target.is_relative_to(tree):
                    raise ValueError('Restore file is outside its project')
                plans.append((label + '/' + item.filename, target, source.read(item)))
    return plans


def restore(root, plans):
    folder = root / 'local/versions' / ('before-v1-restore-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True, exist_ok=False)
    archive = folder / 'current-both-projects.zip'
    originals = {}
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as backup:
        for name, target, _ in plans:
            originals[name] = target.read_bytes() if target.exists() else None
            if originals[name] is not None:
                backup.writestr(name, originals[name])
    with zipfile.ZipFile(archive) as backup:
        if backup.testzip():
            raise ValueError('Current-version backup failed verification; no files restored')
        for name, original in originals.items():
            if original is not None and backup.read(name) != original:
                raise ValueError('Current-version backup mismatch; no files restored')
    changed = []
    try:
        for name, target, body in plans:
            current = target.read_bytes() if target.exists() else None
            if current != originals[name]:
                raise ValueError('A project file changed during restore; please close other editors and retry')
            if current == body:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.restore-tmp')
            temporary.write_bytes(body)
            temporary.replace(target)
            changed.append((name, target))
        for _, target, body in plans:
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(body).digest():
                raise ValueError('Restored file failed verification')
    except Exception:
        for name, target in reversed(changed):
            if originals[name] is None:
                target.unlink()
            else:
                target.write_bytes(originals[name])
        raise
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true', help='Restore both local source trees; never pushes or deploys')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    plans = prepare(root)
    if not args.restore:
        print(json.dumps({'v1': '81901f7', 'v1-web': '39c8536', 'verifiedFiles': len(plans), 'restored': False}))
        return
    backup = restore(root, plans)
    subprocess.run([sys.executable, str(root / 'app/build_viewer.py')], cwd=root, check=True)
    print('Both local source trees restored to v1. Current-version backup: ' + str(backup))
    print('Photos and personal records were preserved. Restart the local library.')
    print('The live website is unchanged. Follow docs/versions.md to commit and publish the web restoration.')


if __name__ == '__main__':
    main()
