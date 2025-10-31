"""Optimised GTA Save Blocker launcher.

This refactored module focuses on resilience and responsiveness. It adds
platform guards, dependency fallbacks, richer configuration handling, and
thread-safe UI updates while preserving the core behaviour of the original
utility.
"""

from __future__ import annotations

import argparse
import atexit
import copy
import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # Optional GUI dependency
    import tkinter as tk  # type: ignore
    from tkinter import messagebox  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tk = None  # type: ignore
    messagebox = None  # type: ignore

try:  # Optional hotkey dependency
    import keyboard  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    keyboard = None  # type: ignore

try:  # Optional tray dependency
    import pystray  # type: ignore
    from pystray import MenuItem  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pystray = None  # type: ignore
    MenuItem = None  # type: ignore

try:  # Optional imaging dependency for tray icon
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

if os.name == "nt":  # pragma: no branch - platform specific
    try:
        import winsound  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        winsound = None  # type: ignore
else:
    winsound = None  # type: ignore


IS_WINDOWS = os.name == "nt"

APP_NAME = "GTA Save Blocker"
VERSION = "3.2.0"
DEFAULT_IP = "192.81.241.171"
DEFAULT_DEBOUNCE = 0.5

CONFIG_ROOT = Path(os.environ.get("GTA_SAVE_BLOCKER_HOME", Path.home() / ".gta_save_blocker")).expanduser()
CONFIG_FILE = CONFIG_ROOT / "config.json"
LOG_FILE = CONFIG_ROOT / "app.log"


def ensure_directory(path: Path) -> None:
    """Ensure the parent directory for the given path exists."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - filesystem error
        print(f"⚠️  Unable to create directory for {path}: {exc}", file=sys.stderr)


def setup_logging(verbose: bool) -> logging.Logger:
    """Configure application-wide logging."""

    ensure_directory(LOG_FILE)

    handlers: List[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - filesystem error
        print(f"⚠️  Unable to open log file {LOG_FILE}: {exc}", file=sys.stderr)

    handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=handlers,
        force=True,
    )
    logger = logging.getLogger(APP_NAME.replace(" ", "_"))
    logger.debug("Logging configured (verbose=%s)", verbose)
    return logger


logger = logging.getLogger(APP_NAME.replace(" ", "_"))


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries into a new dictionary."""

    result = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def play_beep(frequency: int, duration: int) -> None:
    """Attempt to play a beep; fall back to console bell when unavailable."""

    if winsound:  # pragma: no branch - platform specific
        try:
            winsound.Beep(frequency, duration)
            return
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.debug("winsound.Beep failed: %s", exc)

    sys.stdout.write("\a")
    sys.stdout.flush()


class Config:
    """Thread-safe configuration manager with deep-merge support."""

    DEFAULTS: Dict[str, Any] = {
        "hotkeys": {
            "primary": "f9",
            "secondary": "ctrl+alt+s",
        },
        "blocked_ips": [DEFAULT_IP],
        "sound_enabled": True,
        "overlay_visible": True,
        "overlay_position": {"x": 10, "y": 10},
        "auto_cleanup_on_exit": True,
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._loaded = False
        self._data: Dict[str, Any] = {}

    def _defaults(self) -> Dict[str, Any]:
        return copy.deepcopy(self.DEFAULTS)

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if self._loaded:
                return self._data

            config = self._defaults()
            if self.path.exists():
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        payload = json.load(fh)
                    if isinstance(payload, dict):
                        config = deep_merge(config, payload)
                    else:
                        logger.warning("Ignoring invalid config contents (expected dict)")
                except json.JSONDecodeError as exc:
                    logger.warning("Failed to parse config file %s: %s", self.path, exc)
                except OSError as exc:  # pragma: no cover - filesystem error
                    logger.warning("Unable to read config file %s: %s", self.path, exc)

            self._data = config
            self._loaded = True
            logger.debug("Configuration loaded from %s", self.path)
            return self._data

    @property
    def data(self) -> Dict[str, Any]:
        return self.load()

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            config = self.load()
            config[key] = copy.deepcopy(value)
        self.save()

    def save(self) -> None:
        with self._lock:
            if not self._loaded:
                return
            snapshot = copy.deepcopy(self._data)

        ensure_directory(self.path)
        tmp_path = self.path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, indent=2)
            tmp_path.replace(self.path)
            logger.debug("Configuration saved to %s", self.path)
        except OSError as exc:  # pragma: no cover - filesystem error
            logger.error("Failed to persist config %s: %s", self.path, exc)

    def blocked_ips(self) -> List[str]:
        value = self.get("blocked_ips", [DEFAULT_IP])
        if isinstance(value, str):
            ips = [segment.strip() for segment in value.split(",") if segment.strip()]
        elif isinstance(value, Sequence):
            ips = [str(segment).strip() for segment in value if str(segment).strip()]
        else:
            ips = []
        if not ips:
            ips = [DEFAULT_IP]
        return ips

    def hotkeys(self) -> Dict[str, str]:
        value = self.get("hotkeys", {})
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items() if str(v).strip()}


class FirewallManager:
    """Windows firewall rule management with PowerShell fallbacks."""

    RULE_OUT = "GTA_SaveBlock_Out"
    RULE_IN = "GTA_SaveBlock_In"

    def __init__(self, timeout: int = 5) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("FirewallManager requires Windows.")
        self.timeout = timeout
        self._lock = threading.Lock()

    def _powershell_script(self, action: str, ips: Sequence[str] | None = None) -> str:
        if action == "create":
            ip_list = ",".join(f'"{ip}"' for ip in (ips or [DEFAULT_IP]))
            return (
                "$ErrorActionPreference='SilentlyContinue';"
                f"Remove-NetFirewallRule -Name '{self.RULE_OUT}' 2>$null;"
                f"Remove-NetFirewallRule -Name '{self.RULE_IN}' 2>$null;"
                f"New-NetFirewallRule -Name '{self.RULE_OUT}' -DisplayName 'GTA Block Out' "
                f"-Direction Outbound -Action Block -RemoteAddress @({ip_list}) -Protocol Any -Enabled True >$null;"
                f"New-NetFirewallRule -Name '{self.RULE_IN}' -DisplayName 'GTA Block In' "
                f"-Direction Inbound -Action Block -RemoteAddress @({ip_list}) -Protocol Any -Enabled True >$null;"
                "Write-Output 'OK'"
            )
        if action == "remove":
            return (
                "$ErrorActionPreference='SilentlyContinue';"
                f"Remove-NetFirewallRule -Name '{self.RULE_OUT}' 2>$null;"
                f"Remove-NetFirewallRule -Name '{self.RULE_IN}' 2>$null;"
                "Write-Output 'OK'"
            )
        if action == "check":
            return (
                f"$out = Get-NetFirewallRule -Name '{self.RULE_OUT}' -ErrorAction SilentlyContinue;"
                f"$in = Get-NetFirewallRule -Name '{self.RULE_IN}' -ErrorAction SilentlyContinue;"
                "if ($out -and $in) { Write-Output 'EXISTS' } else { Write-Output 'NONE' }"
            )
        raise ValueError(f"Unsupported action: {action}")

    def _execute_ps(self, script: str) -> Optional[str]:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs: Dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": self.timeout,
        }
        if creation_flags:
            kwargs["creationflags"] = creation_flags

        try:
            result = subprocess.run(  # type: ignore [call-overload]
                ["powershell", "-NoProfile", "-Command", script],
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("PowerShell execution failed: %s", exc)
            return None

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            logger.error("PowerShell command failed (%s): %s", result.returncode, stderr)
            return None

        if stderr:
            logger.debug("PowerShell stderr: %s", stderr)
        logger.debug("PowerShell stdout: %s", stdout)
        return stdout

    def create_rules(self, ips: Sequence[str]) -> bool:
        script = self._powershell_script("create", ips)
        response = self._execute_ps(script)
        return response == "OK"

    def remove_rules(self) -> bool:
        script = self._powershell_script("remove")
        response = self._execute_ps(script)
        return response == "OK"

    def check_rules_exist(self) -> bool:
        script = self._powershell_script("check")
        response = self._execute_ps(script)
        return response == "EXISTS"


class PersistentOverlay(tk.Toplevel):  # type: ignore[misc]
    """Always-visible, always-on-top overlay window."""

    def __init__(self, parent: tk.Tk, config: Config):  # type: ignore[assignment]
        super().__init__(parent)
        self._config_ref = config
        self._last_update = 0.0
        self._drag_start: Optional[Tuple[int, int]] = None
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg="#1a1a1a")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.9)
        self._build_ui()
        self._restore_position()
        self.after(2000, self._ensure_on_top)

    def _restore_position(self) -> None:
        pos = self._config_ref.get("overlay_position", {"x": 10, "y": 10})
        try:
            self.geometry(f"+{int(pos['x'])}+{int(pos['y'])}")
        except Exception:
            self.geometry("+10+10")

    def _build_ui(self) -> None:
        frame = tk.Frame(self, bg="#1a1a1a", bd=2, relief=tk.RAISED)
        frame.pack(padx=1, pady=1)

        inner = tk.Frame(frame, bg="#1a1a1a")
        inner.pack(padx=6, pady=6)

        title = tk.Label(
            inner,
            text="GTA SAVE BLOCKER",
            fg="#ffffff",
            bg="#1a1a1a",
            font=("Consolas", 10, "bold"),
        )
        title.pack(pady=(0, 6))

        status_frame = tk.Frame(inner, bg="#1a1a1a")
        status_frame.pack()

        self._status_dot = tk.Label(
            status_frame,
            text="●",
            fg="#ff0000",
            bg="#1a1a1a",
            font=("Arial", 16),
        )
        self._status_dot.pack(side=tk.LEFT, padx=(0, 6))

        self._status_label = tk.Label(
            status_frame,
            text="INACTIVE",
            fg="#888888",
            bg="#1a1a1a",
            font=("Consolas", 9),
        )
        self._status_label.pack(side=tk.LEFT)

        tk.Label(
            inner,
            text="[F9] Toggle",
            fg="#666666",
            bg="#1a1a1a",
            font=("Consolas", 8),
        ).pack(pady=(6, 0))

        close_btn = tk.Label(
            frame,
            text="×",
            fg="#666666",
            bg="#1a1a1a",
            font=("Arial", 12),
            cursor="hand2",
        )
        close_btn.place(relx=0.95, y=2, anchor="ne")
        close_btn.bind("<Button-1>", lambda _event: self.withdraw())

        for sequence, handler in (
            ("<Button-1>", self._start_drag),
            ("<B1-Motion>", self._on_drag),
            ("<ButtonRelease-1>", self._stop_drag),
        ):
            frame.bind(sequence, handler)
            inner.bind(sequence, handler)
            title.bind(sequence, handler)

    def _start_drag(self, event: tk.Event) -> None:  # type: ignore[name-defined]
        self._drag_start = (event.x_root, event.y_root)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[name-defined]
        if not self._drag_start:
            return
        dx = event.x_root - self._drag_start[0]
        dy = event.y_root - self._drag_start[1]
        new_x = self.winfo_x() + dx
        new_y = self.winfo_y() + dy
        self.geometry(f"+{new_x}+{new_y}")
        self._drag_start = (event.x_root, event.y_root)

    def _stop_drag(self, _event: tk.Event) -> None:  # type: ignore[name-defined]
        self._drag_start = None
        position = {"x": self.winfo_x(), "y": self.winfo_y()}
        self._config_ref.set("overlay_position", position)

    def _ensure_on_top(self) -> None:
        try:
            self.lift()
            self.attributes("-topmost", True)
        finally:
            self.after(2000, self._ensure_on_top)

    def set_status(self, active: bool, message: Optional[str] = None) -> None:
        now = time.time()
        if now - self._last_update < 0.09:
            return
        self._last_update = now
        if active:
            self._status_dot.config(fg="#00ff00")
            self._status_label.config(text=message or "BLOCKING", fg="#00ff00")
        else:
            self._status_dot.config(fg="#ff0000")
            self._status_label.config(text=message or "INACTIVE", fg="#888888")
        self.update_idletasks()
        self.lift()


class SystemTrayIcon:
    """System tray integration using pystray when available."""

    def __init__(self, app: "SaveBlocker") -> None:
        if not (pystray and Image and ImageDraw and MenuItem):
            raise RuntimeError("pystray and Pillow are required for tray support")
        self.app = app
        self.icon: Optional[pystray.Icon] = None
        self._icon_cache: Dict[bool, Any] = {}
        self._thread: Optional[threading.Thread] = None

    def _icon_image(self, active: bool):
        if active in self._icon_cache:
            return self._icon_cache[active]
        image = Image.new("RGB", (32, 32), color="black")
        draw = ImageDraw.Draw(image)
        colour = "#00ff00" if active else "#ff0000"
        draw.ellipse([8, 8, 24, 24], fill=colour)
        self._icon_cache[active] = image
        return image

    def start(self) -> None:
        menu = pystray.Menu(
            MenuItem("Toggle (F9)", lambda _icon, _item: self.app.request_toggle_blocking()),
            MenuItem("Show/Hide Overlay", lambda _icon, _item: self.app.request_overlay_toggle()),
            pystray.Menu.SEPARATOR,
            MenuItem("Exit", lambda _icon, _item: self.app.request_exit()),
        )
        self.icon = pystray.Icon(APP_NAME, self._icon_image(False), APP_NAME, menu)
        self._thread = threading.Thread(target=self.icon.run, name="tray-thread", daemon=True)
        self._thread.start()
        logger.debug("System tray icon started")

    def update(self, active: bool) -> None:
        if self.icon:
            self.icon.icon = self._icon_image(active)

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception as exc:  # pragma: no cover - dependency specific
                logger.debug("Tray stop raised: %s", exc)
            self.icon = None


class SaveBlocker:
    """Main application controller orchestrating UI and firewall state."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.config = Config(args.config)
        self.firewall = FirewallManager(timeout=args.firewall_timeout)
        self.active = False
        self._last_toggle = 0.0
        self._toggle_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gta-save-blocker")
        self._toggle_task: Optional[Future[None]] = None
        self._stop_event = threading.Event()

        self.headless = args.headless or tk is None
        self.root: Optional[tk.Tk] = None  # type: ignore[assignment]
        self.overlay: Optional[PersistentOverlay] = None

        if not self.headless:
            assert tk is not None  # for type checkers
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.title(APP_NAME)
            self.overlay = PersistentOverlay(self.root, self.config)
            if self.config.get("overlay_visible", True):
                self.overlay.deiconify()
                self.overlay.lift()
        else:
            if tk is None and not args.headless:
                logger.warning("tkinter not available; falling back to headless mode")

        self.tray: Optional[SystemTrayIcon] = None
        if args.no_tray:
            logger.info("System tray disabled via CLI flag")
        elif pystray and Image and ImageDraw and MenuItem:
            try:
                self.tray = SystemTrayIcon(self)
                self.tray.start()
            except Exception as exc:
                logger.warning("Failed to initialise system tray: %s", exc)
                self.tray = None
        else:
            if not args.no_tray:
                logger.info("System tray dependencies unavailable; tray disabled")

        self._setup_hotkeys(disable=args.no_hotkeys)
        self._setup_cleanup()
        self.ensure_clean_start()

    def _setup_hotkeys(self, disable: bool) -> None:
        if disable:
            logger.info("Hotkeys disabled via CLI flag")
            return
        if keyboard is None:
            logger.warning("keyboard module unavailable; hotkeys disabled")
            return

        hotkeys = self.config.hotkeys()
        primary = hotkeys.get("primary", "f9")
        secondary = hotkeys.get("secondary", "ctrl+alt+s")

        for label, combo in (("primary", primary), ("secondary", secondary)):
            try:
                keyboard.add_hotkey(combo, self.request_toggle_blocking, suppress=True)
                logger.info("Registered %s hotkey: %s", label, combo)
            except Exception as exc:  # pragma: no cover - dependency specific
                logger.warning("Failed to register %s hotkey '%s': %s", label, combo, exc)

    def _setup_cleanup(self) -> None:
        atexit.register(self._cleanup_handler)
        try:
            signal.signal(signal.SIGINT, lambda *_: self.request_exit())
            signal.signal(signal.SIGTERM, lambda *_: self.request_exit())
        except Exception:  # pragma: no cover - platform specific
            logger.debug("Signal handlers not available on this platform")

    def ensure_clean_start(self) -> None:
        try:
            self.firewall.remove_rules()
        except Exception as exc:
            logger.warning("Initial firewall cleanup failed: %s", exc)
        self.active = False
        self._notify_status(False, "INACTIVE")
        self._print_console("✅ Firewall rules cleared - GTA is NOT blocked")

    def request_toggle_blocking(self) -> None:
        if self._stop_event.is_set():
            return
        now = time.monotonic()
        if now - self._last_toggle < self.args.debounce:
            logger.debug("Toggle ignored due to debounce window")
            return
        self._last_toggle = now

        if self.root:
            self.root.after(0, self._schedule_toggle_task)
        else:
            self._schedule_toggle_task()

    def request_overlay_toggle(self) -> None:
        if not self.overlay:
            self._print_console("ℹ️  Overlay not available in headless mode")
            return

        def _toggle() -> None:
            assert self.overlay  # for type checkers
            if self.overlay.winfo_viewable():
                self.overlay.withdraw()
                self.config.set("overlay_visible", False)
                self._print_console("👁️  Overlay hidden")
            else:
                self.overlay.deiconify()
                self.overlay.lift()
                self.config.set("overlay_visible", True)
                self._print_console("👁️  Overlay shown")

        self._run_on_ui_thread(_toggle)

    def request_exit(self) -> None:
        self._run_on_ui_thread(self.quit)

    def _schedule_toggle_task(self) -> None:
        if self._toggle_task and not self._toggle_task.done():
            logger.debug("Toggle already in progress; ignoring new request")
            return
        self._toggle_task = self._executor.submit(self._toggle_worker)

        def _reset(_future: Future[Any]) -> None:
            self._toggle_task = None

        self._toggle_task.add_done_callback(_reset)

    def _toggle_worker(self) -> None:
        with self._toggle_lock:
            current_state = self.active

        if not current_state:
            self._notify_status(False, "ACTIVATING…")
            success = self._activate_blocking()
            if success:
                with self._toggle_lock:
                    self.active = True
                self._notify_status(True, "BLOCKING")
                self._print_console(f"🔴 BLOCKING ACTIVE - {datetime.now().strftime('%H:%M:%S')}")
                if self.config.get("sound_enabled", True):
                    self._executor.submit(play_beep, 800, 150)
            else:
                self._notify_status(False, "FAILED")
                self._print_console("❌ Failed to activate blocking")
        else:
            self._notify_status(True, "DEACTIVATING…")
            success = self._deactivate_blocking()
            if success:
                with self._toggle_lock:
                    self.active = False
                self._notify_status(False, "INACTIVE")
                self._print_console(f"🟢 BLOCKING REMOVED - {datetime.now().strftime('%H:%M:%S')}")
                if self.config.get("sound_enabled", True):
                    self._executor.submit(play_beep, 400, 150)
            else:
                self._notify_status(True, "FAILED")
                self._print_console("❌ Failed to remove blocking")

    def _activate_blocking(self) -> bool:
        ips = self.config.blocked_ips()
        logger.debug("Activating firewall rules for IPs: %s", ips)
        return self.firewall.create_rules(ips)

    def _deactivate_blocking(self) -> bool:
        logger.debug("Removing firewall rules")
        return self.firewall.remove_rules()

    def _notify_status(self, active: bool, message: Optional[str] = None) -> None:
        status_message = message or ("BLOCKING" if active else "INACTIVE")

        def _update() -> None:
            if self.overlay:
                self.overlay.set_status(active, status_message)
            if self.tray:
                self.tray.update(active)

        self._run_on_ui_thread(_update)

    def _run_on_ui_thread(self, func: Callable[[], None]) -> None:
        if self.root:
            self.root.after(0, func)
        else:
            func()

    def _print_console(self, message: str) -> None:
        print(message, flush=True)

    def _cleanup_handler(self) -> None:
        if not self.config.get("auto_cleanup_on_exit", True):
            return
        if self.active:
            try:
                self.firewall.remove_rules()
                logger.info("Firewall rules removed during cleanup")
            except Exception as exc:
                logger.warning("Failed to remove firewall rules during cleanup: %s", exc)

    def run(self) -> None:
        self._print_banner()
        if self.root:
            self.root.protocol("WM_DELETE_WINDOW", self.request_exit)
            try:
                self.root.mainloop()
            finally:
                self._executor.shutdown(wait=False)
        else:
            try:
                while not self._stop_event.is_set():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                self.request_exit()
            finally:
                self._executor.shutdown(wait=False)

    def quit(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._print_console("\n👋 Shutting down…")

        if self.active:
            if self.firewall.remove_rules():
                self._print_console("✅ Firewall rules removed")
            else:
                self._print_console("⚠️  Unable to confirm firewall cleanup")

        if self.tray:
            self.tray.stop()

        if self.root:
            self.root.after(0, self.root.quit)

    def _print_banner(self) -> None:
        print(f"\n{'=' * 50}")
        print(f" {APP_NAME} v{VERSION}")
        print(f"{'=' * 50}")
        print("\n📌 Controls:")
        if keyboard:
            print("  • Press [F9] to toggle save blocking")
            print("  • Alternative: [Ctrl+Alt+S]")
        else:
            print("  • Hotkeys unavailable (keyboard module missing)")
        if self.tray:
            print("  • Right-click tray icon for menu")
        if self.overlay and self.overlay.winfo_exists():
            print("  • Drag overlay to reposition")
        print("\n✅ Status: INACTIVE (GTA not blocked)")
        print(f"{'=' * 50}\n")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimised GTA Save Blocker controller")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE, help="Override configuration file path")
    parser.add_argument("--headless", action="store_true", help="Disable Tk overlay even if Tk is available")
    parser.add_argument("--no-hotkeys", action="store_true", help="Disable global keyboard hotkeys")
    parser.add_argument("--no-tray", action="store_true", help="Disable system tray icon")
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE, help="Minimum seconds between toggle events")
    parser.add_argument("--firewall-timeout", type=int, default=5, help="Timeout in seconds for PowerShell commands")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--no-elevation", action="store_true", help="Do not prompt for administrator elevation")
    return parser.parse_args(argv)


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - platform specific
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - platform specific
        return False


def request_elevation() -> None:
    try:
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None,
            "runas",
            sys.executable,
            " ".join(sys.argv),
            None,
            1,
        )
    except Exception as exc:  # pragma: no cover - platform specific
        logger.error("Failed to request elevation: %s", exc)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if not IS_WINDOWS:
        print("❌ This application currently supports Windows only (needs Windows Firewall).", file=sys.stderr)
        return 2

    args = parse_args(argv)

    global logger
    logger = setup_logging(args.verbose)

    ensure_directory(args.config)

    if not is_admin():
        print("\n⚠️  Administrator privileges required!", flush=True)
        if args.no_elevation:
            print("❌ Run this utility as Administrator.", file=sys.stderr)
            return 3
        print("Requesting elevation...", flush=True)
        request_elevation()
        return 0

    try:
        app = SaveBlocker(args)
        app.run()
        return 0
    except Exception as exc:  # pragma: no cover - runtime guard
        logger.critical("Fatal error: %s", exc, exc_info=exc)
        if messagebox:
            messagebox.showerror("Error", f"Application failed:\n{exc}")
        else:
            print(f"❌ Application failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - script entry
    sys.exit(main())
