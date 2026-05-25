# GTA Save Blocker

A Windows utility that blocks Grand Theft Auto V / GTA Online from communicating with Rockstar's save-game servers. By creating Windows Firewall rules, it prevents the game from saving — useful for players who want to replay missions, test scenarios, or avoid unwanted auto-saves.

## Features

- **One-click toggle** — Enable/disable save blocking with a hotkey (`F9` or `Ctrl+Alt+S`)
- **System tray icon** — Right-click menu for toggle, show/hide overlay, and exit
- **Desktop overlay** — Always-on-top semi-transparent status indicator (red = inactive, green = blocking), draggable and persistent position
- **Configurable IPs** — Block custom IP addresses via JSON config
- **Hotkeys** — Global hotkeys work even when the game is in focus
- **Headless mode** — CLI-only operation with `--headless` for advanced users
- **Sound feedback** — Audio cues on state changes
- **Automatic cleanup** — Firewall rules are removed on exit

## Requirements

- **Windows** (7, 8, 10, or 11) — this tool relies on PowerShell and Windows Firewall
- **Python 3.12+**
- Administrator privileges (required to modify firewall rules)

### Optional Dependencies

| Dependency | Purpose |
|---|---|
| `tkinter` (stdlib) | Desktop overlay window |
| `keyboard` | Global hotkey registration |
| `pystray` + `Pillow` | System tray icon |

All optional dependencies gracefully degrade — the app runs without them, just with reduced UI.

## Installation

```bash
pip install keyboard pystray Pillow
```

Or install only what you need:

```bash
pip install keyboard   # for hotkeys
```

## Usage

Run from an **Administrator** command prompt:

```bash
python main.py
```

### Command-line options

| Argument | Description |
|---|---|
| `--headless` | Disable the desktop overlay |
| `--no-hotkeys` | Disable global hotkeys |
| `--no-tray` | Disable the system tray icon |
| `--no-elevation` | Don't prompt for administrator elevation |
| `--config PATH` | Path to custom config file |
| `--debounce SECONDS` | Minimum seconds between toggles (default: 0.5) |
| `--firewall-timeout SECS` | Timeout in seconds for PowerShell commands (default: 5) |
| `--verbose`, `-v` | Enable verbose logging |

### Controls

- **`F9`** or **`Ctrl+Alt+S`** — Toggle blocking on/off
- **System tray icon** — Right-click for menu
- **Overlay** — Click and drag to reposition

## How it works

The app uses PowerShell (`netsh` / `NetSecurity` module) to create Windows Firewall rules that block inbound and outbound traffic to Rockstar's save-game server IP (`192.81.241.171` by default). When toggled off, those rules are removed.

Configuration is stored in `~/.gta_save_blocker/config.json`.

## License

This project is provided as-is. No license is specified.
