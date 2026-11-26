import gzip
import importlib.util
import json
from pathlib import Path


def test_compresses_archive_and_updates_manifest(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'output' / '.github' / 'prepare_pages.py'
    spec = importlib.util.spec_from_file_location('prepare_pages', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = tmp_path / 'arquivos'
    archive.mkdir()
    (archive / 'day.json').write_text('{"data": "teste"}', encoding='utf-8')
    (archive / 'day.md').write_text('histórico completo', encoding='utf-8')
    manifest = {'files': [{'json_path': 'arquivos/day.json', 'markdown_path': 'arquivos/day.md'}]}
    (archive / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    module.prepare(tmp_path)

    updated = json.loads((archive / 'manifest.json').read_text())
    for key, expected in [('json_path', '{"data": "teste"}'), ('markdown_path', 'histórico completo')]:
        path = tmp_path / updated['files'][0][key]
        assert path.suffix == '.gz'
        assert gzip.open(path, 'rt', encoding='utf-8').read() == expected
        assert not path.with_suffix('').exists()
