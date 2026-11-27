"""Windows taskbar identity for this Python-hosted app and its shortcuts."""
import ctypes
from ctypes import wintypes as W
import json
from pathlib import Path
import subprocess
import sys
import uuid

APP_ID = 'Personal.DesktopDock.Launcher'
APP_NAME = 'Desktop Launcher'


class GUID(ctypes.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', W.BYTE * 8)]

    @classmethod
    def parse(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class PropertyKey(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', W.DWORD)]


class Blob(ctypes.Structure):
    _fields_ = [('count', W.ULONG), ('pointer', ctypes.c_void_p)]


class VariantValue(ctypes.Union):
    _fields_ = [('pointer', ctypes.c_void_p), ('number', ctypes.c_longlong), ('blob', Blob)]


class PropVariant(ctypes.Structure):
    _fields_ = [('vt', W.WORD), ('r1', W.WORD), ('r2', W.WORD), ('r3', W.WORD), ('value', VariantValue)]


shell = ctypes.WinDLL('shell32')
ole = ctypes.OleDLL('ole32')
STORE_IID = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
PROPERTY_GUID = GUID.parse('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3')
PROPERTIES = {'RelaunchCommand': 2, 'RelaunchIcon': 3, 'DisplayName': 4, 'AppId': 5}
shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [W.LPCWSTR]
shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
shell.SHGetPropertyStoreForWindow.argtypes = [W.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long
shell.SHGetPropertyStoreFromParsingName.argtypes = [W.LPCWSTR, ctypes.c_void_p, W.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
shell.SHGetPropertyStoreFromParsingName.restype = ctypes.c_long
ole.PropVariantClear.argtypes = [ctypes.POINTER(PropVariant)]


def checked(result):
    if result < 0:
        raise OSError(f'Windows taskbar identity error 0x{result & 0xffffffff:08X}')


def configure_process_identity():
    checked(shell.SetCurrentProcessExplicitAppUserModelID(APP_ID))


class PropertyStore:
    def __init__(self, hwnd=None, path=None):
        # S_FALSE means COM was already initialized in the same apartment.
        result = ole.CoInitialize(None)
        self.uninitialize = result in (0, 1)
        self.pointer = ctypes.c_void_p()
        try:
            if hwnd is not None:
                checked(shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(STORE_IID), ctypes.byref(self.pointer)))
            else:
                checked(shell.SHGetPropertyStoreFromParsingName(str(path), None, 2, ctypes.byref(STORE_IID), ctypes.byref(self.pointer)))
            self.vtable = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        except Exception:
            if self.uninitialize:
                ole.CoUninitialize()
            raise

    def method(self, index, *arguments):
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(self.vtable[index])

    def set(self, name, value):
        key = PropertyKey(PROPERTY_GUID, PROPERTIES[name])
        text = ctypes.create_unicode_buffer(value)
        variant = PropVariant()
        variant.vt = 31  # VT_LPWSTR; SetValue copies this caller-owned buffer.
        variant.value.pointer = ctypes.cast(text, ctypes.c_void_p)
        checked(self.method(6, ctypes.POINTER(PropertyKey), ctypes.POINTER(PropVariant))(self.pointer, ctypes.byref(key), ctypes.byref(variant)))

    def get(self, name):
        key = PropertyKey(PROPERTY_GUID, PROPERTIES[name])
        variant = PropVariant()
        try:
            checked(self.method(5, ctypes.POINTER(PropertyKey), ctypes.POINTER(PropVariant))(self.pointer, ctypes.byref(key), ctypes.byref(variant)))
            return ctypes.wstring_at(variant.value.pointer) if variant.vt == 31 and variant.value.pointer else None
        finally:
            ole.PropVariantClear(ctypes.byref(variant))

    def commit(self):
        checked(self.method(7)(self.pointer))

    def close(self):
        self.method(2)(self.pointer)
        if self.uninitialize:
            ole.CoUninitialize()


def app_properties(script, icon):
    pythonw = str(Path(sys.executable).with_name('pythonw.exe'))
    return {'RelaunchCommand': subprocess.list2cmdline([pythonw, str(script), '--app', '--show']),
            'RelaunchIcon': str(icon) + ',0', 'DisplayName': APP_NAME, 'AppId': APP_ID}


def set_properties(values, **target):
    store = PropertyStore(**target)
    try:
        # Relaunch properties precede AppId, as required by the Windows shell.
        for name, value in values.items():
            store.set(name, value)
        store.commit()
    finally:
        store.close()


def read_properties(**target):
    store = PropertyStore(**target)
    try:
        return {name: store.get(name) for name in PROPERTIES}
    finally:
        store.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--shortcut', required=True, type=Path)
    parser.add_argument('--script', required=True, type=Path)
    parser.add_argument('--icon', required=True, type=Path)
    args = parser.parse_args()
    set_properties(app_properties(args.script, args.icon), path=args.shortcut)
    print(json.dumps(read_properties(path=args.shortcut), indent=2))
