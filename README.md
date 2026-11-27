# Desktop Launcher

The “part of atria” header link opens the [Atria app directory](https://atria.jealrodos.xyz/icons/atria/apps.html)
in your default browser. It also supports keyboard activation.

A small Windows launcher for your own apps, files and folders. It groups
shortcuts into Daily, Create, Build, Play and Tools, with search, native Windows
icons and running-app indicators.

Desktop Launcher is part of Atria. It uses the Solarized dark palette, an
outlined Othala rune and a lowercase wordmark. A Solarized light appearance is
also available. The application and its bundled icon helper make no network
requests.

## Install

Requires Windows, Python 3.12 or newer with Tcl/Tk, and Windows PowerShell 5.1.
No Python packages are required.

Download or clone this repository, then run from its directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Setup.ps1
```

The execution-policy option applies to this invocation only; it does not
change the computer's policy. Setup installs program files under
`%LOCALAPPDATA%\Programs\DesktopLauncher` and adds a Start menu shortcut.
It does not launch the app or enable startup automatically.

Open **Desktop Launcher** from Start. A new installation begins with File
Explorer; use **+ Add**, the right-click menu and the five categories to build
your collection. Pin the application's own taskbar button if desired.

For a specific Python interpreter or destination:

```powershell
.\Setup.ps1 -Python 'C:\Path\To\Python\python.exe' -InstallDirectory 'C:\Path\To\DesktopLauncher'
```

Use `-NoShortcuts` to skip Start menu changes. Setup backs up existing program
files before replacing them and keeps an existing configuration and pins.
Close a running launcher before updating its program files.

## Preferences and local data

Pins, icon caches, runtime status and error logs stay in
`%APPDATA%\DesktopDock`. None of those files belongs in source control.
The example configuration is an empty starter, not a copy of a real user's
shortcuts.

Preferences control light/dark appearance, icon size and optional startup.
The normal application uses a resizable window; the source also retains its
earlier dock/preview mode. The power-mode control uses Windows' native overlay
API when the operating system supports it.

```powershell
pythonw.exe desktop_dock.pyw --app --show
python.exe desktop_dock.pyw --self-test .\artifacts\self-test.json
```

Run these from the installed directory after setup; create `artifacts` first.
The self-test checks your configured targets and cached icons without opening
the targets. The native app identity remains `Personal.DesktopDock.Launcher`.

To remove the program, delete its installation folder and Start menu shortcut.
Disable **Start with Windows** in Preferences first if you enabled it.
Retain `%APPDATA%\DesktopDock` to keep your collection, or remove that folder
separately when you no longer want the saved data.

## Source and assets

- `desktop_dock.pyw`: Python/Tk application.
- `windows_app.py`: native Windows taskbar identity and shortcut metadata.
- `Prepare-Icons.ps1`: local icon extraction and PNG cache generation.
- `Setup.ps1`: per-user setup, without changing saved pins or startup.
- `atria-app.ico`: the application icon, derived from the existing Atria rune.
- `atria-app.svg`: the square application artwork used to generate the ICO.
- `atria-rune.svg`: the original Othala vector source.
- `atria-wordmark.ttf`: privately loaded Josefin Sans font.

The rune geometry is retained from the existing Atria identity; it is not a
new logo. The font is included under the SIL Open Font License in
[`atria-font-OFL.txt`](atria-font-OFL.txt). This repository does not currently
include a license for the authored application code.

## Validation

```powershell
python tools/check_release.py
python tools/check_release.py --native
python tools/check_secrets.py
```

The native check builds a fresh profile in a temporary directory, renders the
app and exercises its categories, search and themes without opening
shortcuts or changing the real user's settings. The secret scan uses a
checksum-pinned Gitleaks release.
