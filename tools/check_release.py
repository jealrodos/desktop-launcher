"""Validate a source release; optional Windows checks use an isolated profile."""
import argparse
import ast
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROGRAM_FILES = ('desktop_dock.pyw', 'windows_app.py', 'Prepare-Icons.ps1',
                 'atria-wordmark.ttf', 'atria-font-OFL.txt', 'atria-app.ico')


def source_check():
    for name in (*PROGRAM_FILES, 'atria-rune.svg', 'atria-app.svg', 'Setup.ps1', 'config.example.json'):
        assert (ROOT / name).is_file(), 'Missing release file: ' + name
    for name in ('desktop_dock.pyw', 'windows_app.py'):
        ast.parse((ROOT / name).read_text(encoding='utf-8'), filename=name)
    config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    assert config['Items'] == [] and config['SelectedGroup'] == 'Daily'
    assert config['IconSize'] in (32, 40, 48, 56, 64, 72)
    data = (ROOT / 'atria-app.ico').read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data)
    assert reserved == 0 and kind == 1 and count == 7
    sizes = set()
    for index in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
        width, height = width or 256, height or 256
        assert width == height and 6 + count * 16 <= offset < offset + length <= len(data)
        assert data[offset:offset + 8] == b'\x89PNG\r\n\x1a\n'
        sizes.add(width)
    assert sizes == {16, 24, 32, 48, 64, 128, 256}
    for name in ('atria-rune.svg', 'atria-app.svg'):
        svg = ET.fromstring((ROOT / name).read_text(encoding='utf-8'))
        assert svg.tag.endswith('svg')
        assert not any(el.tag.split('}')[-1] in ('script', 'image', 'foreignObject') for el in svg.iter())
        def check_outline(element, inherited='black'):
            fill = element.get('fill', inherited)
            if element.tag.split('}')[-1] == 'path':
                assert fill == 'none', 'The rune must retain its outlined, unfilled shape'
            for child in element:
                check_outline(child, fill)
        check_outline(svg)
    assert not (ROOT / 'config.json').exists(), 'A runtime configuration must not be in the repository'
    print('Source, empty starter profile, SVGs and seven-resolution ICO validated.')


def native_child(install, profile):
    # Check the isolation boundary before importing code that opens APPDATA.
    profile = profile.resolve()
    assert Path(os.environ['APPDATA']).resolve() == profile
    assert (profile.parent / '.launcher-validation').read_text() == 'isolated'
    sys.path.insert(0, str(install))
    loader = importlib.machinery.SourceFileLoader('launcher_validation', str(install / 'desktop_dock.pyw'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.APP_DIR.resolve().is_relative_to(profile)
    # A preview must not register the user's hotkey or enumerate other apps.
    module.app_windows = lambda: {}
    module.Dock.hotkey_loop = lambda self: self.hotkey_ready.set()
    module.Dock.write_runtime = lambda self: None
    dock = None
    checks = {}
    try:
        dock = module.Dock(module.load_config(), preview=True, app=True)
        dock.root.withdraw()
        dock.root.update_idletasks()
        checks['native_window_constructed'] = dock.root.title() == 'Desktop Launcher'
        checks['cream_wordmark'] = all(label.cget('fg') == '#eee8d5' for label in dock.app_wordmark_labels)
        checks['private_font'] = dock.atria_font == 'Josefin Sans'
        for group in module.GROUPS:
            dock.select_group(group)
            dock.root.update_idletasks()
            expected = 1 + sum(pin['Group'] == group for pin in dock.config['Items'])
            checks['category_' + group] = len(dock.app_visible_items) == expected and len(dock.images) == expected
        dock.search_text.set('Example')
        dock.render_app()
        checks['search'] = [pin['Id'] for pin in dock.app_visible_items] == ['example']
        dock.search_text.set('missing-shortcut')
        dock.render_app()
        checks['empty_search'] = len(dock.app_visible_items) == 0
        dock.search_text.set('')
        dock.config['LightTheme'] = True
        dock.render_app()
        checks['light_theme'] = dock.bg == '#fdf6e3' and all(label.cget('fg') == '#073642' for label in dock.app_wordmark_labels)
        checks['native_self_test'] = module.self_test(module.load_config())['Passed']
    finally:
        if dock:
            dock.shutdown_hotkey()
            dock.root.destroy()
    print(json.dumps(checks, sort_keys=True))
    assert all(checks.values()), checks


def native_check():
    assert platform.system() == 'Windows', 'Native validation requires Windows'
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix='atria-launcher-check-', dir=temporary_parent) as temporary:
        temp = Path(temporary).resolve()
        assert temp.is_relative_to(temporary_parent) and temp.name.startswith('atria-launcher-check-')
        (temp / '.launcher-validation').write_text('isolated')
        install, profile = temp / 'program', temp / 'profile'
        env = dict(os.environ, APPDATA=str(profile), LOCALAPPDATA=str(temp / 'local'))
        setup = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(ROOT / 'Setup.ps1'), '-Python', sys.executable,
                 '-InstallDirectory', str(install), '-NoShortcuts']
        result = subprocess.run(setup, env=env, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        config_path = profile / 'DesktopDock/config.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        assert config['Items'] == []
        explorer = str(Path(os.environ['WINDIR']) / 'explorer.exe')
        config['Items'] = [{'Id': 'example', 'Name': 'Example Explorer', 'Target': explorer,
                            'Group': 'Daily', 'IconPath': explorer, 'Arguments': '',
                            'WorkingDirectory': '', 'ProcessName': 'explorer'}]
        before = (json.dumps(config, separators=(',', ':')) + '\n').encode()
        config_path.write_bytes(before)
        result = subprocess.run(setup, env=env, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert config_path.read_bytes() == before, 'Setup overwrote saved preferences'
        assert (profile / 'DesktopDock/DesktopDock.ico').read_bytes() == (ROOT / 'atria-app.ico').read_bytes()
        for name in PROGRAM_FILES:
            assert (install / name).read_bytes() == (ROOT / name).read_bytes()
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--native-child',
                                 '--install', str(install), '--profile', str(profile)],
                                env=env, text=True, capture_output=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
    print('Fresh installation, update preservation and native rendering validated in an isolated profile.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--native-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--install', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--profile', type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.native_child:
        native_child(arguments.install, arguments.profile)
    else:
        source_check()
        if arguments.native:
            native_check()
