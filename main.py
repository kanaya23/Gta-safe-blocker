import os
import sys
import ctypes
import subprocess
import tkinter as tk
from tkinter import messagebox
import keyboard
import threading
import time
import json
import logging
from datetime import datetime
from pathlib import Path
import winsound
from typing import Optional, Dict, Any, List
import pystray
from PIL import Image, ImageDraw
import atexit
import signal
from functools import lru_cache

# Constants
APP_NAME = "GTA Save Blocker"
VERSION = "3.0.1"
CONFIG_FILE = Path.home() / ".gta_save_blocker" / "config.json"
LOG_FILE = Path.home() / ".gta_save_blocker" / "app.log"
DEFAULT_IP = "192.81.241.171"

# Setup directories
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Optimized logging - only log important events
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, mode='a')]
)
logger = logging.getLogger(__name__)


class Config:
    """Lightweight configuration management"""
    
    DEFAULT_CONFIG = {
        "hotkeys": {
            "primary": "f9",
            "secondary": "ctrl+alt+s"
        },
        "blocked_ips": [DEFAULT_IP],
        "sound_enabled": True,
        "overlay_visible": True,
        "overlay_position": {"x": 10, "y": 10},
        "auto_cleanup_on_exit": True
    }
    
    def __init__(self):
        self._config = None
        self._load_lock = threading.Lock()
        
    @property
    def config(self):
        if self._config is None:
            with self._load_lock:
                if self._config is None:
                    self._config = self.load()
        return self._config
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    return {**self.DEFAULT_CONFIG, **loaded}
        except:
            pass
        return self.DEFAULT_CONFIG.copy()
    
    def save(self):
        """Save configuration asynchronously"""
        def _save():
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(self._config, f, indent=2)
            except:
                pass
        
        threading.Thread(target=_save, daemon=True).start()
    
    def get(self, key: str, default=None):
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any):
        self.config[key] = value
        self.save()


class FirewallManager:
    """Optimized firewall management"""
    
    def __init__(self):
        self.rule_name_out = "GTA_SaveBlock_Out"
        self.rule_name_in = "GTA_SaveBlock_In"
        self._ps_cache = {}
        
    @lru_cache(maxsize=2)
    def _get_ps_command(self, action: str, ips: tuple = None) -> str:
        """Cache PowerShell commands for reuse"""
        if action == "create":
            ip_list = ','.join(f'"{ip}"' for ip in (ips or (DEFAULT_IP,)))
            return f"""
            $ErrorActionPreference='SilentlyContinue'
            Remove-NetFirewallRule -Name '{self.rule_name_out}' 2>$null
            Remove-NetFirewallRule -Name '{self.rule_name_in}' 2>$null
            New-NetFirewallRule -Name '{self.rule_name_out}' -DisplayName 'GTA Block Out' -Direction Outbound -Action Block -RemoteAddress @({ip_list}) -Protocol Any -Enabled True >$null
            New-NetFirewallRule -Name '{self.rule_name_in}' -DisplayName 'GTA Block In' -Direction Inbound -Action Block -RemoteAddress @({ip_list}) -Protocol Any -Enabled True >$null
            Write-Output 'OK'
            """
        elif action == "remove":
            return f"""
            $ErrorActionPreference='SilentlyContinue'
            Remove-NetFirewallRule -Name '{self.rule_name_out}' 2>$null
            Remove-NetFirewallRule -Name '{self.rule_name_in}' 2>$null
            Write-Output 'OK'
            """
        elif action == "check":
            return f"""
            $out = Get-NetFirewallRule -Name '{self.rule_name_out}' -ErrorAction SilentlyContinue
            $in = Get-NetFirewallRule -Name '{self.rule_name_in}' -ErrorAction SilentlyContinue
            if ($out -and $in) {{ Write-Output 'EXISTS' }} else {{ Write-Output 'NONE' }}
            """
    
    def _execute_ps(self, command: str, timeout: int = 5) -> Optional[str]:
        """Execute PowerShell command with short timeout"""
        try:
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', command],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.stdout.strip()
        except:
            return None
    
    def create_rules(self, ips: List[str] = None) -> bool:
        """Create firewall rules quickly"""
        ips_tuple = tuple(ips) if ips else (DEFAULT_IP,)
        command = self._get_ps_command("create", ips_tuple)
        result = self._execute_ps(command)
        return result == 'OK' if result else False
    
    def remove_rules(self) -> bool:
        """Remove firewall rules quickly"""
        command = self._get_ps_command("remove")
        result = self._execute_ps(command)
        return result == 'OK' if result else False
    
    def check_rules_exist(self) -> bool:
        """Quick check if rules exist"""
        command = self._get_ps_command("check")
        result = self._execute_ps(command)
        return result == 'EXISTS' if result else False


class PersistentOverlay(tk.Toplevel):
    """Always-visible, always-on-top overlay window"""
    
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.setup_window()
        self.create_widgets()
        self._last_update = 0
        
        # Keep window on top periodically
        self.ensure_on_top()
        
    def setup_window(self):
        """Configure window to always stay visible and on top"""
        self.title("")
        
        # Load saved position or use default
        pos = self.config.get("overlay_position", {"x": 10, "y": 10})
        self.geometry(f"+{pos['x']}+{pos['y']}")
        
        self.configure(bg='#1a1a1a')
        
        # Critical: Make window stay on top
        self.attributes('-topmost', True)
        self.attributes('-alpha', 0.9)  # Slightly more visible
        self.overrideredirect(True)
        
        # Remove toolwindow to ensure visibility
        # self.attributes('-toolwindow', True)  # Removed to prevent hiding
        
        # Ensure window stays on top of fullscreen applications
        self.wm_attributes('-topmost', True)
        self.lift()
        
        # Make draggable
        self.bind('<Button-1>', self._start_drag)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._stop_drag)
        
    def _start_drag(self, event):
        """Start dragging"""
        self._drag_x = event.x
        self._drag_y = event.y
        
    def _on_drag(self, event):
        """Handle dragging"""
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")
    
    def _stop_drag(self, event):
        """Stop dragging and save position"""
        self.config.set("overlay_position", {
            "x": self.winfo_x(),
            "y": self.winfo_y()
        })
    
    def ensure_on_top(self):
        """Periodically ensure window stays on top"""
        try:
            self.lift()
            self.attributes('-topmost', True)
            # Check every 2 seconds
            self.after(2000, self.ensure_on_top)
        except:
            pass
    
    def create_widgets(self):
        """Create overlay widgets"""
        # Main frame with border
        frame = tk.Frame(self, bg='#1a1a1a', bd=2, relief=tk.RAISED)
        frame.pack(padx=1, pady=1)
        
        # Inner container
        inner = tk.Frame(frame, bg='#1a1a1a')
        inner.pack(padx=5, pady=5)
        
        # Title
        tk.Label(
            inner,
            text="GTA SAVE BLOCKER",
            fg='#ffffff',
            bg='#1a1a1a',
            font=('Consolas', 10, 'bold')
        ).pack(pady=(0, 5))
        
        # Status indicator frame
        status_frame = tk.Frame(inner, bg='#1a1a1a')
        status_frame.pack()
        
        # Status dot (using label for simplicity)
        self.status_dot = tk.Label(
            status_frame,
            text="●",
            fg='#ff0000',
            bg='#1a1a1a',
            font=('Arial', 16)
        )
        self.status_dot.pack(side=tk.LEFT, padx=(0, 5))
        
        # Status text
        self.status_label = tk.Label(
            status_frame,
            text="INACTIVE",
            fg='#888888',
            bg='#1a1a1a',
            font=('Consolas', 9)
        )
        self.status_label.pack(side=tk.LEFT)
        
        # Hotkey reminder
        tk.Label(
            inner,
            text="[F9] Toggle",
            fg='#666666',
            bg='#1a1a1a',
            font=('Consolas', 8)
        ).pack(pady=(5, 0))
        
        # Close button (small X in corner)
        close_btn = tk.Label(
            frame,
            text="×",
            fg='#666666',
            bg='#1a1a1a',
            font=('Arial', 12),
            cursor='hand2'
        )
        close_btn.place(relx=0.95, y=2, anchor='ne')
        close_btn.bind('<Button-1>', lambda e: self.withdraw())
    
    def set_status(self, active: bool, message: str = None):
        """Update status display"""
        current_time = time.time()
        if current_time - self._last_update < 0.1:  # Rate limit
            return
        
        self._last_update = current_time
        
        if active:
            self.status_dot.config(fg='#00ff00')
            self.status_label.config(
                text=message or "BLOCKING",
                fg='#00ff00'
            )
        else:
            self.status_dot.config(fg='#ff0000')
            self.status_label.config(
                text=message or "INACTIVE",
                fg='#888888'
            )
        
        # Force update and ensure on top
        self.update_idletasks()
        self.lift()


class SystemTrayIcon:
    """Optimized system tray icon"""
    
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._icon_cache = {}
        
    def create_icon_image(self, active: bool) -> Image:
        """Create or get cached icon image"""
        if active not in self._icon_cache:
            image = Image.new('RGB', (32, 32), color='black')
            draw = ImageDraw.Draw(image)
            color = '#00ff00' if active else '#ff0000'
            draw.ellipse([8, 8, 24, 24], fill=color)
            self._icon_cache[active] = image
        return self._icon_cache[active]
    
    def create(self):
        """Create system tray icon"""
        menu = pystray.Menu(
            pystray.MenuItem("Toggle (F9)", self.app.toggle_blocking),
            pystray.MenuItem("Show/Hide Overlay", self.app.toggle_overlay),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.app.quit)
        )
        
        self.icon = pystray.Icon(
            APP_NAME,
            self.create_icon_image(False),
            APP_NAME,
            menu
        )
    
    def update(self, active: bool):
        """Update icon efficiently"""
        if self.icon:
            self.icon.icon = self.create_icon_image(active)
    
    def run(self):
        """Run in background thread"""
        self.create()
        threading.Thread(target=self.icon.run, daemon=True).start()


class SaveBlocker:
    """Optimized Save Blocker main class"""
    
    def __init__(self):
        self.config = Config()
        self.firewall = FirewallManager()
        self.active = False
        self._toggle_lock = threading.Lock()
        self._last_toggle = 0
        
        # GUI setup
        self.root = tk.Tk()
        self.root.withdraw()  # Hide main window
        
        # Create persistent overlay
        self.overlay = PersistentOverlay(self.root, self.config)
        
        # System tray
        self.tray = SystemTrayIcon(self)
        self.tray.run()
        
        # Setup
        self.setup_hotkeys()
        self.setup_cleanup()
        
        # IMPORTANT: Ensure starts deactivated and clean
        self.ensure_clean_start()
        
        # Show overlay if configured
        if self.config.get("overlay_visible", True):
            self.overlay.deiconify()
            self.overlay.lift()
        
        logger.info(f"{APP_NAME} initialized")
    
    def ensure_clean_start(self):
        """Ensure GTA is NOT blocked on startup"""
        # Force remove any existing rules
        self.firewall.remove_rules()
        self.active = False
        self.overlay.set_status(False, "INACTIVE")
        self.tray.update(False)
        print("✅ Firewall rules cleared - GTA is NOT blocked")
        
    def setup_hotkeys(self):
        """Register hotkeys efficiently"""
        hotkeys = self.config.get("hotkeys", {})
        
        # Primary hotkey
        primary = hotkeys.get("primary", "f9")
        try:
            keyboard.add_hotkey(primary, self.toggle_blocking, suppress=True)
            print(f"✅ Hotkey registered: {primary.upper()}")
        except:
            print(f"⚠️  Failed to register hotkey: {primary}")
        
        # Secondary hotkey
        secondary = hotkeys.get("secondary", "ctrl+alt+s")
        try:
            keyboard.add_hotkey(secondary, self.toggle_blocking, suppress=True)
            print(f"✅ Alternative hotkey: {secondary.upper()}")
        except:
            pass
    
    def setup_cleanup(self):
        """Setup cleanup handlers"""
        def cleanup():
            if self.active:
                self.firewall.remove_rules()
                print("🧹 Cleanup: Firewall rules removed")
        
        atexit.register(cleanup)
        signal.signal(signal.SIGINT, lambda s, f: self.quit())
        signal.signal(signal.SIGTERM, lambda s, f: self.quit())
    
    def toggle_blocking(self):
        """Toggle blocking with debounce and thread safety"""
        current_time = time.time()
        
        # Debounce (500ms)
        if current_time - self._last_toggle < 0.5:
            return
        
        with self._toggle_lock:
            self._last_toggle = current_time
            
            # Ensure overlay is visible when toggling
            if not self.overlay.winfo_viewable():
                self.overlay.deiconify()
                self.overlay.lift()
            
            if not self.active:
                # Activate blocking
                self.overlay.set_status(False, "ACTIVATING...")
                
                if self.firewall.create_rules([DEFAULT_IP]):
                    self.active = True
                    self.overlay.set_status(True, "BLOCKING")
                    self.tray.update(True)
                    
                    if self.config.get("sound_enabled", True):
                        threading.Thread(
                            target=lambda: winsound.Beep(800, 150),
                            daemon=True
                        ).start()
                    
                    print(f"🔴 BLOCKING ACTIVE - {datetime.now().strftime('%H:%M:%S')}")
                else:
                    self.overlay.set_status(False, "FAILED!")
                    time.sleep(1)
                    self.overlay.set_status(False, "INACTIVE")
                    print("❌ Failed to activate blocking")
            else:
                # Deactivate blocking
                self.overlay.set_status(True, "DEACTIVATING...")
                
                if self.firewall.remove_rules():
                    self.active = False
                    self.overlay.set_status(False, "INACTIVE")
                    self.tray.update(False)
                    
                    if self.config.get("sound_enabled", True):
                        threading.Thread(
                            target=lambda: winsound.Beep(400, 150),
                            daemon=True
                        ).start()
                    
                    print(f"🟢 BLOCKING REMOVED - {datetime.now().strftime('%H:%M:%S')}")
                else:
                    self.overlay.set_status(True, "FAILED!")
                    time.sleep(1)
                    self.overlay.set_status(True, "BLOCKING")
                    print("❌ Failed to remove blocking")
    
    def toggle_overlay(self):
        """Toggle overlay visibility"""
        if self.overlay.winfo_viewable():
            self.overlay.withdraw()
            self.config.set("overlay_visible", False)
            print("👁️  Overlay hidden")
        else:
            self.overlay.deiconify()
            self.overlay.lift()
            self.config.set("overlay_visible", True)
            print("👁️  Overlay shown")
    
    def quit(self):
        """Clean quit"""
        print("\n👋 Shutting down...")
        
        if self.active:
            self.firewall.remove_rules()
            print("✅ Firewall rules removed")
        
        if hasattr(self, 'tray') and self.tray.icon:
            self.tray.icon.stop()
        
        self.root.quit()
        sys.exit(0)
    
    def run(self):
        """Run the application"""
        print(f"\n{'='*50}")
        print(f" {APP_NAME} v{VERSION}")
        print(f"{'='*50}")
        print("\n📌 Controls:")
        print("  • Press [F9] to toggle save blocking")
        print("  • Alternative: [Ctrl+Alt+S]")
        print("  • Right-click tray icon for menu")
        print("  • Drag overlay to reposition")
        print("\n✅ Status: INACTIVE (GTA not blocked)")
        print(f"{'='*50}\n")
        
        # Main loop
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()


def is_admin() -> bool:
    """Check admin privileges"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def main():
    """Main entry point"""
    # Check admin
    if not is_admin():
        print("\n⚠️  Administrator privileges required!")
        print("Requesting elevation...")
        
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable,
                " ".join(sys.argv), None, 1
            )
        except:
            print("❌ Failed to elevate. Please run as Administrator.")
            input("\nPress Enter to exit...")
        sys.exit(0)
    
    try:
        app = SaveBlocker()
        app.run()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        messagebox.showerror("Error", f"Application failed:\n{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
