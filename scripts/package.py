"""Verify an allowlisted source archive through isolated real-SDK discovery."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'_manifest.json', 'README.md', 'CHANGELOG.md', 'LICENSE'}
SOURCE_DIRS = {'apis', 'codecs', 'runtime', 'services'}


def source_files():
    for path in ROOT.rglob('*'):
        name = path.relative_to(ROOT)
        if path.is_symlink() or not path.is_file() or any(p.startswith('.') for p in name.parts):
            continue
        if (name.as_posix() in ROOT_FILES or (len(name.parts) == 1 and path.suffix == '.py')
                or (name.parts[0] in SOURCE_DIRS and path.suffix == '.py')
                or name.as_posix() in {'assets/icon.jpg', 'assets/icon-v2.png'}):
            yield name.as_posix(), path.read_bytes()


if __name__ == '__main__':
    with TemporaryDirectory(prefix='snowluma-package-') as tmp:
        directory = Path(tmp)
        artifact = directory / 'source.zip'
        members = dict(source_files())
        assert 'runtime/router.py' in members and '_manifest.json' in members
        icon = json.loads(members['_manifest.json'])['display']['icon']
        assert icon['type'] != 'local' or icon['value'] in members
        with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                if name.endswith('.py'):
                    compile(data, name, 'exec')
                archive.writestr(name, data)
        unpacked = directory / 'unpacked'
        with zipfile.ZipFile(artifact) as archive:
            assert set(archive.namelist()) == set(members)
            archive.extractall(unpacked)
        assert all((unpacked / name).read_bytes() == data for name, data in members.items())
        program = '''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('isolated_adapter', root/'plugin.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from maibot_sdk import MaiBotPlugin
plugin = module.create_plugin()
assert isinstance(plugin, MaiBotPlugin)
assert any(item['type'] == 'MESSAGE_GATEWAY' for item in plugin.get_components())
assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
assert json.loads((root/'_manifest.json').read_text(encoding='utf-8'))['id'] == 'maibot-team.snowluma-adapter'
print('Isolated adapter package / real SDK / gateway discovery passed; no on_load or QQ')
'''
        subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', program, str(unpacked)], cwd=tmp, check=True)
        print(json.dumps({'members': len(members), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}))
