"""Compress archive files in a disposable Pages build checkout."""
import gzip
import json
import shutil
from pathlib import Path


def prepare(root):
    manifest_path = root / 'arquivos' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for entry in manifest['files']:
        for key in ('json_path', 'markdown_path'):
            source = root / entry[key]
            target = source.with_name(source.name + '.gz')
            with source.open('rb') as original, gzip.open(target, 'wb', compresslevel=6) as compressed:
                shutil.copyfileobj(original, compressed)
            source.unlink()
            entry[key] += '.gz'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    prepare(Path('.'))
