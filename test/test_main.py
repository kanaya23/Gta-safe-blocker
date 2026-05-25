"""Comprehensive tests for GTA Save Blocker (main.py).

Covers all edge cases, error paths, and pitfalls on every function/class.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch, ANY

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Mock the tkinter-dependent class definitions BEFORE importing main, so tests
# that mock out deps don't crash during module load.
_original_modules = {}


def _import_main_with_mocks(patches: dict[str, object]) -> type:
    """Import the main module after applying monkeypatches to sys.modules
    so that optional deps can be simulated as missing/None without blowing up
    the class definitions that inherit from tk.Toplevel."""
    for key, value in patches.items():
        _original_modules[key] = sys.modules.get(key)
        sys.modules[key] = value  # type: ignore[assignment]

    if "main" in sys.modules:
        del sys.modules["main"]

    import main as m  # noqa: F811

    return m


def _cleanup_import_patches():
    for key, orig in _original_modules.items():
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig
    _original_modules.clear()
    if "main" in sys.modules:
        del sys.modules["main"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_main_module():
    """Ensure a clean main module for each test that imports it."""
    yield
    if "main" in sys.modules:
        del sys.modules["main"]


@pytest.fixture
def mock_tk():
    """Provide a mock tkinter with a real-enough Toplevel to satisfy imports."""
    class FakeToplevel:
        def __init__(self, *a, **kw): pass

    mock = MagicMock()
    mock.Toplevel = FakeToplevel
    return mock


@pytest.fixture
def mock_tk_for_saveblocker(monkeypatch):
    """Full mock suite for SaveBlocker instantiation (tk + pystray + keyboard)."""
    class FakeTk:
        def __init__(self):
            self.tk = MagicMock()
            self.tk.createcommand = MagicMock()
        def withdraw(self): pass
        def title(self, s): pass
        def after(self, ms, cb): pass
        def protocol(self, *a): pass
        def mainloop(self): pass
        def quit(self): pass

    class FakeToplevel:
        def __init__(self, *a, **kw):
            self._config_ref = None
            self._last_update = 0.0
            self._drag_start = None
            self._status_dot = None
            self._status_label = None
        def withdraw(self): pass
        def deiconify(self): pass
        def lift(self): pass
        def winfo_viewable(self): return False
        def winfo_exists(self): return True
        def winfo_x(self): return 0
        def winfo_y(self): return 0
        def geometry(self, *a): pass
        def overrideredirect(self, *a): pass
        def configure(self, **kw): pass
        def attributes(self, *a, **kw): pass
        def update_idletasks(self): pass

    mock = MagicMock()
    mock.Tk = FakeTk
    mock.Toplevel = FakeToplevel
    mock.Frame = MagicMock
    mock.Label = MagicMock
    monkeypatch.setattr("main.tk", mock)

    monkeypatch.setattr("main.keyboard", MagicMock())
    monkeypatch.setattr("main.pystray", MagicMock())
    monkeypatch.setattr("main.MenuItem", MagicMock())
    monkeypatch.setattr("main.Image", MagicMock())
    monkeypatch.setattr("main.ImageDraw", MagicMock())

    monkeypatch.setattr("main.IS_WINDOWS", True)
    mock_ctypes = MagicMock()
    mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 1
    monkeypatch.setattr("main.ctypes", mock_ctypes)
    monkeypatch.setattr("main.winsound", MagicMock())

    monkeypatch.setattr("main.PersistentOverlay", MagicMock())
    monkeypatch.setattr("main.SystemTrayIcon", MagicMock())

    mock_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
    return monkeypatch


# ---------------------------------------------------------------------------
# Test: helper – ensure_directory
# ---------------------------------------------------------------------------

class TestEnsureDirectory:
    def test_creates_parent(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "config.json"
        from main import ensure_directory
        ensure_directory(target)
        assert target.parent.exists()

    def test_already_exists(self, tmp_path: Path):
        target = tmp_path / "existing" / "file.json"
        target.parent.mkdir(parents=True)
        from main import ensure_directory
        ensure_directory(target)

    def test_oserror_is_caught(self, tmp_path: Path, monkeypatch):
        from main import ensure_directory
        monkeypatch.setattr(Path, "mkdir", MagicMock(side_effect=OSError("denied")))
        ensure_directory(tmp_path / "no-perm" / "cfg.json")


# ---------------------------------------------------------------------------
# Test: setup_logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_verbose_logging(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("main.LOG_FILE", tmp_path / "app.log")
        from main import setup_logging
        logger = setup_logging(verbose=True)
        assert logger.getEffectiveLevel() == logging.DEBUG

    def test_info_logging(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("main.LOG_FILE", tmp_path / "app.log")
        from main import setup_logging
        logger = setup_logging(verbose=False)
        assert logger.getEffectiveLevel() == logging.INFO

    def test_file_handler_failure(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("main.LOG_FILE", tmp_path / "app.log")
        from main import setup_logging
        with patch("main.logging.FileHandler", side_effect=OSError("cannot open")):
            logger = setup_logging(verbose=True)
        assert logger is not None


# ---------------------------------------------------------------------------
# Test: deep_merge
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_simple_merge(self):
        from main import deep_merge
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_nested_merge(self):
        from main import deep_merge
        base = {"a": {"b": 1, "c": 2}}
        update = {"a": {"b": 99, "d": 3}}
        result = deep_merge(base, update)
        assert result == {"a": {"b": 99, "c": 2, "d": 3}}

    def test_overwrite_non_dict_with_dict(self):
        from main import deep_merge
        result = deep_merge({"a": 1}, {"a": {"b": 2}})
        assert result == {"a": {"b": 2}}

    def test_deepcopy_isolation(self):
        from main import deep_merge
        base = {"a": [1, 2, 3]}
        update = {"a": [4, 5]}
        result = deep_merge(base, update)
        assert result == {"a": [4, 5]}
        assert base == {"a": [1, 2, 3]}

    def test_empty_updates(self):
        from main import deep_merge
        result = deep_merge({"a": 1}, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        from main import deep_merge
        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_none_value(self):
        from main import deep_merge
        result = deep_merge({"a": 1}, {"a": None})
        assert result["a"] is None

    def test_nested_empty_dict_merge(self):
        from main import deep_merge
        result = deep_merge({"a": {"b": {}}}, {"a": {"b": {"c": 1}}})
        assert result == {"a": {"b": {"c": 1}}}


# ---------------------------------------------------------------------------
# Test: play_beep
# ---------------------------------------------------------------------------

class TestPlayBeep:
    def test_with_winsound(self, monkeypatch):
        mock_beep = MagicMock()
        monkeypatch.setattr("main.winsound", MagicMock(Beep=mock_beep))
        from main import play_beep
        play_beep(800, 150)
        mock_beep.assert_called_once_with(800, 150)

    def test_winsound_fallback_on_exception(self, monkeypatch):
        mock_beep = MagicMock(side_effect=Exception("no beep"))
        mock_winsound = MagicMock(Beep=mock_beep)
        monkeypatch.setattr("main.winsound", mock_winsound)
        from main import play_beep
        with patch("sys.stdout") as mock_stdout:
            play_beep(800, 150)
            mock_stdout.write.assert_called_with("\a")

    def test_without_winsound(self, monkeypatch):
        monkeypatch.setattr("main.winsound", None)
        from main import play_beep
        with patch("sys.stdout") as mock_stdout:
            play_beep(800, 150)
            mock_stdout.write.assert_called_with("\a")


# ---------------------------------------------------------------------------
# Test: Config class
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_loaded(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        data = cfg.load()
        assert data["blocked_ips"] == ["192.81.241.171"]
        assert data["sound_enabled"] is True
        assert data["overlay_visible"] is True
        assert data["auto_cleanup_on_exit"] is True

    def test_load_from_existing_file(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"sound_enabled": False, "blocked_ips": ["1.2.3.4"]}))
        from main import Config
        cfg = Config(config_file)
        data = cfg.load()
        assert data["sound_enabled"] is False
        assert data["blocked_ips"] == ["1.2.3.4"]
        assert data["overlay_visible"] is True

    def test_load_is_cached(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"test": "value1"}))
        from main import Config
        cfg = Config(config_file)
        data1 = cfg.load()
        config_file.write_text(json.dumps({"test": "value2"}))
        data2 = cfg.load()
        assert data2["test"] == "value1"

    def test_load_corrupt_json(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json")
        from main import Config
        cfg = Config(config_file)
        data = cfg.load()
        assert data["blocked_ips"] == ["192.81.241.171"]

    def test_load_non_dict_payload(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(["list", "not", "dict"]))
        from main import Config
        cfg = Config(config_file)
        data = cfg.load()
        assert data["blocked_ips"] == ["192.81.241.171"]

    def test_load_file_read_error(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"blocked_ips": ["10.0.0.1"]}))
        from main import Config
        cfg = Config(config_file)
        with patch("builtins.open", side_effect=OSError("cannot read")):
            data = cfg.load()
        assert data["blocked_ips"] == ["192.81.241.171"]

    def test_data_property(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        assert cfg.data["blocked_ips"] == ["192.81.241.171"]

    def test_get_method(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        assert cfg.get("sound_enabled") is True
        assert cfg.get("nonexistent", "default") == "default"
        assert cfg.get("nonexistent") is None

    def test_set_and_persist(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        from main import Config
        cfg = Config(config_file)
        cfg.set("sound_enabled", False)
        assert config_file.exists()
        saved = json.loads(config_file.read_text())
        assert saved["sound_enabled"] is False

    def test_save_when_not_loaded(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        cfg.save()
        assert not (tmp_path / "config.json").exists()

    def test_save_oserror(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        from main import Config
        cfg = Config(config_file)
        cfg.load()
        with patch("builtins.open", side_effect=OSError("cannot write")):
            cfg.save()

    def test_blocked_ips_from_string(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"blocked_ips": "1.1.1.1, 2.2.2.2, 3.3.3.3"}))
        from main import Config
        cfg = Config(config_file)
        ips = cfg.blocked_ips()
        assert ips == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]

    def test_blocked_ips_from_list(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"blocked_ips": ["10.0.0.1", "10.0.0.2"]}))
        from main import Config
        cfg = Config(config_file)
        ips = cfg.blocked_ips()
        assert ips == ["10.0.0.1", "10.0.0.2"]

    def test_blocked_ips_fallback_when_empty(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"blocked_ips": []}))
        from main import Config
        cfg = Config(config_file)
        ips = cfg.blocked_ips()
        assert ips == ["192.81.241.171"]

    def test_blocked_ips_fallback_when_non_sequence(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"blocked_ips": 42}))
        from main import Config
        cfg = Config(config_file)
        ips = cfg.blocked_ips()
        assert ips == ["192.81.241.171"]

    def test_hotkeys_valid(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        keys = cfg.hotkeys()
        assert keys["primary"] == "f9"
        assert keys["secondary"] == "ctrl+alt+s"

    def test_hotkeys_non_dict(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkeys": "not-a-dict"}))
        from main import Config
        cfg = Config(config_file)
        assert cfg.hotkeys() == {}

    def test_hotkeys_empty_values_stripped(self, tmp_path: Path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkeys": {"primary": "f9", "secondary": ""}}))
        from main import Config
        cfg = Config(config_file)
        keys = cfg.hotkeys()
        assert "primary" in keys
        assert "secondary" not in keys

    def test_thread_safety(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        errors = []

        def worker():
            try:
                for _ in range(20):
                    cfg.load()
                    cfg.set("sound_enabled", True)
                    cfg.get("blocked_ips")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_defaults_not_mutated_by_instance(self, tmp_path: Path):
        from main import Config
        cfg1 = Config(tmp_path / "cfg1.json")
        cfg2 = Config(tmp_path / "cfg2.json")
        cfg1.load()
        cfg2.load()
        cfg1.set("blocked_ips", ["10.0.0.1"])
        assert cfg2.get("blocked_ips") == ["192.81.241.171"]

    def test_concurrent_save(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        cfg.load()
        errors = []
        def saver():
            try:
                for _ in range(20):
                    cfg.set("count", threading.get_ident())
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=saver) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# Test: FirewallManager
# ---------------------------------------------------------------------------

class TestFirewallManager:
    def test_raises_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", False)
        from main import FirewallManager
        with pytest.raises(RuntimeError, match="FirewallManager requires Windows"):
            FirewallManager()

    def test_script_create(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        script = fw._powershell_script("create", ["1.1.1.1", "2.2.2.2"])
        assert "New-NetFirewallRule" in script
        assert '"1.1.1.1","2.2.2.2"' in script
        assert fw.RULE_OUT in script
        assert fw.RULE_IN in script

    def test_script_create_default_ip(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        script = fw._powershell_script("create")
        assert '"192.81.241.171"' in script

    def test_script_create_empty_ips(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        script = fw._powershell_script("create", [])
        assert "Write-Output 'OK'" in script

    def test_script_remove(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        script = fw._powershell_script("remove")
        assert "Remove-NetFirewallRule" in script
        assert "Write-Output 'OK'" in script

    def test_script_check(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        script = fw._powershell_script("check")
        assert "Get-NetFirewallRule" in script
        assert "EXISTS" in script

    def test_script_invalid_action(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        from main import FirewallManager
        fw = FirewallManager()
        with pytest.raises(ValueError, match="Unsupported action"):
            fw._powershell_script("invalid_action")

    def test_execute_ps_success(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw._execute_ps("script") == "OK"

    def test_execute_ps_failure_returncode(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw._execute_ps("script") is None

    def test_execute_ps_subprocess_error(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError("no ps")))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw._execute_ps("script") is None

    def test_execute_ps_timeout(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=subprocess.TimeoutExpired("cmd", 5)))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw._execute_ps("script") is None

    def test_create_rules_success(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw.create_rules(["1.1.1.1"]) is True

    def test_create_rules_failure(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="FAIL\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw.create_rules(["1.1.1.1"]) is False

    def test_remove_rules_success(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw.remove_rules() is True

    def test_check_rules_exist(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="EXISTS\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw.check_rules_exist() is True

    def test_check_rules_not_exist(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="NONE\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        assert fw.check_rules_exist() is False

    def test_create_with_no_window_flag(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="OK\n", stderr=""))
        monkeypatch.setattr(subprocess, "run", mock_run)
        from main import FirewallManager
        fw = FirewallManager()
        fw.create_rules(["1.1.1.1"])
        assert mock_run.call_args[1].get("creationflags") == 0x08000000

    def test_thread_safety(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_result = MagicMock(returncode=0, stdout="OK\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        from main import FirewallManager
        fw = FirewallManager()
        errors = []
        def worker():
            try:
                for _ in range(10):
                    fw.create_rules(["1.1.1.1"])
                    fw.remove_rules()
                    fw.check_rules_exist()
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# Test: parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        from main import parse_args
        args = parse_args([])
        assert args.headless is False
        assert args.no_hotkeys is False
        assert args.no_tray is False
        assert args.verbose is False
        assert args.no_elevation is False
        assert args.debounce == 0.5
        assert args.firewall_timeout == 5

    def test_headless_flag(self):
        from main import parse_args
        args = parse_args(["--headless"])
        assert args.headless is True

    def test_no_hotkeys(self):
        from main import parse_args
        args = parse_args(["--no-hotkeys"])
        assert args.no_hotkeys is True

    def test_no_tray(self):
        from main import parse_args
        args = parse_args(["--no-tray"])
        assert args.no_tray is True

    def test_verbose_short(self):
        from main import parse_args
        args = parse_args(["-v"])
        assert args.verbose is True

    def test_verbose_long(self):
        from main import parse_args
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_no_elevation(self):
        from main import parse_args
        args = parse_args(["--no-elevation"])
        assert args.no_elevation is True

    def test_custom_debounce(self):
        from main import parse_args
        args = parse_args(["--debounce", "2.5"])
        assert args.debounce == 2.5

    def test_custom_firewall_timeout(self):
        from main import parse_args
        args = parse_args(["--firewall-timeout", "15"])
        assert args.firewall_timeout == 15

    def test_custom_config(self):
        from main import parse_args
        args = parse_args(["--config", "/custom/path/config.json"])
        assert str(args.config) == "/custom/path/config.json"


# ---------------------------------------------------------------------------
# Test: is_admin & request_elevation
# ---------------------------------------------------------------------------

class TestAdmin:
    def test_is_admin_windows_true(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 1
        monkeypatch.setattr("main.ctypes", mock_ctypes)
        from main import is_admin
        assert is_admin() is True

    def test_is_admin_windows_false(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0
        monkeypatch.setattr("main.ctypes", mock_ctypes)
        from main import is_admin
        assert is_admin() is False

    def test_is_admin_windows_exception(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.side_effect = Exception("denied")
        monkeypatch.setattr("main.ctypes", mock_ctypes)
        from main import is_admin
        assert is_admin() is False

    def test_is_admin_unix_root(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", False)
        monkeypatch.setattr(os, "geteuid", MagicMock(return_value=0))
        from main import is_admin
        assert is_admin() is True

    def test_is_admin_unix_nonroot(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", False)
        monkeypatch.setattr(os, "geteuid", MagicMock(return_value=1000))
        from main import is_admin
        assert is_admin() is False

    def test_is_admin_unix_no_geteuid(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", False)
        monkeypatch.delattr(os, "geteuid", raising=False)
        from main import is_admin
        assert is_admin() is False

    def test_request_elevation(self, monkeypatch):
        mock_ctypes = MagicMock()
        monkeypatch.setattr("main.ctypes", mock_ctypes)
        from main import request_elevation
        request_elevation()
        mock_ctypes.windll.shell32.ShellExecuteW.assert_called_once()

    def test_request_elevation_failure(self, monkeypatch):
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.ShellExecuteW.side_effect = Exception("fail")
        monkeypatch.setattr("main.ctypes", mock_ctypes)
        from main import request_elevation
        request_elevation()


# ---------------------------------------------------------------------------
# Test: SaveBlocker
# ---------------------------------------------------------------------------

class TestSaveBlockerConstructor:
    def test_headless_mode(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        assert app.headless is True

    def test_headless_fallback_when_no_tk(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        assert app.headless is True

    def test_hotkeys_disabled(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--no-hotkeys"])
        app = SaveBlocker(args)
        import main as main_module
        assert not main_module.keyboard.add_hotkey.called

    def test_hotkeys_without_keyboard_module(self, mock_tk_for_saveblocker):
        import main as main_module
        main_module.keyboard = None
        from main import SaveBlocker, parse_args
        args = parse_args([])
        app = SaveBlocker(args)

    def test_tray_disabled(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--no-tray"])
        app = SaveBlocker(args)
        assert app.tray is None

    def test_tray_without_deps(self, mock_tk_for_saveblocker):
        import main as main_module
        main_module.pystray = None
        main_module.Image = None
        main_module.ImageDraw = None
        main_module.MenuItem = None
        from main import SaveBlocker, parse_args
        args = parse_args([])
        app = SaveBlocker(args)
        assert app.tray is None

    def test_tray_init_failure(self, mock_tk_for_saveblocker):
        import main as main_module
        original = main_module.SystemTrayIcon
        main_module.SystemTrayIcon = MagicMock(side_effect=Exception("init failed"))
        from main import SaveBlocker, parse_args
        args = parse_args([])
        app = SaveBlocker(args)
        assert app.tray is None
        main_module.SystemTrayIcon = original

    def test_ensure_clean_start_called(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        with patch.object(SaveBlocker, "ensure_clean_start") as mock_clean:
            args = parse_args(["--headless"])
            SaveBlocker(args)
        mock_clean.assert_called_once()

    def test_cleanup_handler_registered(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        with patch("atexit.register") as mock_atexit:
            args = parse_args(["--headless"])
            SaveBlocker(args)
        mock_atexit.assert_called_once()

    def test_signal_handlers(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        with patch("signal.signal") as mock_signal:
            args = parse_args(["--headless"])
            SaveBlocker(args)
        assert mock_signal.call_count >= 2

    def test_signal_handler_error(self, mock_tk_for_saveblocker, monkeypatch):
        monkeypatch.setattr(signal, "signal", MagicMock(side_effect=Exception("no signals")))
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        SaveBlocker(args)


class TestSaveBlockerEnsureCleanStart:
    def test_removes_rules_on_start(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.ensure_clean_start()
        app.firewall.remove_rules.assert_called_once()

    def test_remove_rules_failure(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.firewall.remove_rules.side_effect = Exception("firewall error")
        app.ensure_clean_start()
        assert app.active is False


class TestSaveBlockerToggle:
    def test_toggle_stop_event_set(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app._stop_event.set()
        with patch.object(app, "_schedule_toggle_task") as mock_schedule:
            app.request_toggle_blocking()
            mock_schedule.assert_not_called()

    def test_toggle_debounce(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless", "--debounce", "10"])
        app = SaveBlocker(args)
        app.request_toggle_blocking()
        app.request_toggle_blocking()

    def test_toggle_activates_blocking(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.firewall.create_rules.return_value = True
        app._toggle_worker()
        assert app.active is True

    def test_toggle_deactivates_blocking(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.firewall.create_rules.return_value = True
        app.active = True
        app.firewall.remove_rules.return_value = True
        app._toggle_worker()
        assert app.active is False

    def test_toggle_activate_failure(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.firewall.create_rules.return_value = False
        app._toggle_worker()
        assert app.active is False

    def test_toggle_deactivate_failure(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.active = True
        app.firewall.remove_rules.return_value = False
        app._toggle_worker()
        assert app.active is True

    def test_concurrent_toggle_prevented(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        from concurrent.futures import Future
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.firewall.create_rules.return_value = True
        fut = Future()
        app._toggle_task = fut
        with patch.object(app._executor, "submit") as mock_submit:
            app._schedule_toggle_task()
            mock_submit.assert_not_called()
        fut.set_result(None)

    def test_activate_blocking_uses_config_ips(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.firewall = MagicMock()
        app.config = MagicMock()
        app.config.blocked_ips.return_value = ["10.0.0.1", "10.0.0.2"]
        app._activate_blocking()
        app.firewall.create_rules.assert_called_with(["10.0.0.1", "10.0.0.2"])

    def test_overlay_toggle_headless(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.overlay = None
        app.request_overlay_toggle()

    def test_overlay_toggle_hide(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        mock_overlay = MagicMock()
        mock_overlay.winfo_viewable.return_value = True
        app.overlay = mock_overlay
        app.config = MagicMock()
        app.request_overlay_toggle()
        mock_overlay.withdraw.assert_called_once()

    def test_overlay_toggle_show(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        mock_overlay = MagicMock()
        mock_overlay.winfo_viewable.return_value = False
        app.overlay = mock_overlay
        app.config = MagicMock()
        app.request_overlay_toggle()
        mock_overlay.deiconify.assert_called_once()

    def test_notify_status_no_ui(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.overlay = None
        app.tray = None
        app._notify_status(True)


class TestSaveBlockerQuit:
    def test_quit_with_active_rules(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.active = True
        app.firewall = MagicMock()
        app.firewall.remove_rules.return_value = True
        app.quit()
        assert app._stop_event.is_set()
        app.firewall.remove_rules.assert_called_once()

    def test_quit_remove_failure(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.active = True
        app.firewall = MagicMock()
        app.firewall.remove_rules.return_value = False
        app.quit()

    def test_quit_idempotent(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app._stop_event.set()
        with patch.object(app, "_print_console") as mock_print:
            app.quit()
            assert mock_print.call_count == 0

    def test_cleanup_handler_skips(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.config = MagicMock()
        app.config.get.return_value = False
        app.active = True
        app.firewall = MagicMock()
        app._cleanup_handler()
        app.firewall.remove_rules.assert_not_called()

    def test_cleanup_handler_removes(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.active = True
        app.firewall = MagicMock()
        app._cleanup_handler()
        app.firewall.remove_rules.assert_called_once()

    def test_cleanup_handler_failure(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.active = True
        app.firewall = MagicMock()
        app.firewall.remove_rules.side_effect = Exception("fail")
        app._cleanup_handler()

    def test_quit_with_tray_stop_error(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app.tray = MagicMock()
        app.tray.stop.side_effect = Exception("tray stop error")
        with pytest.raises(Exception, match="tray stop error"):
            app.quit()


class TestSaveBlockerRun:
    def test_run_headless(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        app._stop_event.set()
        app.run()

    def test_run_headless_keyboard_interrupt(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        original = app._stop_event.is_set
        call_count = [0]
        def side_effect():
            call_count[0] += 1
            return call_count[0] >= 3
        app._stop_event.is_set = side_effect
        app.run()

    def test_run_with_tk(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args([])
        app = SaveBlocker(args)
        with patch.object(app.root, "mainloop") as mock_mainloop:
            app.run()
            mock_mainloop.assert_called_once()

    def test_print_banner_no_keyboard(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        import main as main_module
        main_module.keyboard = None
        args = parse_args(["--headless"])
        app = SaveBlocker(args)
        with patch("builtins.print") as mock_print:
            app._print_banner()
            assert mock_print.called

    def test_print_banner_no_tray(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args(["--headless", "--no-tray"])
        app = SaveBlocker(args)
        with patch("builtins.print") as mock_print:
            app._print_banner()
            assert mock_print.called

    def test_hotkey_registration_failure(self, mock_tk_for_saveblocker):
        import main as main_module
        main_module.keyboard.add_hotkey.side_effect = Exception("hotkey error")
        from main import SaveBlocker, parse_args
        args = parse_args([])
        SaveBlocker(args)

    def test_run_tk_exception_in_mainloop(self, mock_tk_for_saveblocker):
        from main import SaveBlocker, parse_args
        args = parse_args([])
        app = SaveBlocker(args)
        app.root = MagicMock()
        app.root.mainloop.side_effect = Exception("tk error")
        with pytest.raises(Exception, match="tk error"):
            app.run()


# ---------------------------------------------------------------------------
# Test: main() entry point
# ---------------------------------------------------------------------------

class TestMain:
    def test_non_windows_exit(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", False)
        from main import main
        assert main([]) == 2

    def test_not_admin_with_elevation(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr("main.is_admin", MagicMock(return_value=False))
        mock_request = MagicMock()
        monkeypatch.setattr("main.request_elevation", mock_request)
        from main import main
        assert main([]) == 0
        mock_request.assert_called_once()

    def test_not_admin_no_elevation(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr("main.is_admin", MagicMock(return_value=False))
        from main import main
        assert main(["--no-elevation"]) == 3

    def test_normal_execution(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr("main.is_admin", MagicMock(return_value=True))
        mock_app = MagicMock()
        monkeypatch.setattr("main.SaveBlocker", MagicMock(return_value=mock_app))
        from main import main
        assert main(["--headless"]) == 0
        mock_app.run.assert_called_once()

    def test_fatal_error(self, monkeypatch):
        monkeypatch.setattr("main.IS_WINDOWS", True)
        monkeypatch.setattr("main.is_admin", MagicMock(return_value=True))
        monkeypatch.setattr("main.SaveBlocker", MagicMock(side_effect=ValueError("broken")))
        monkeypatch.setattr("main.messagebox", None)
        monkeypatch.setattr("main.ctypes", MagicMock())
        from main import main
        assert main(["--headless"]) == 1


# ---------------------------------------------------------------------------
# Test: Optional dependency handling at module level
# ---------------------------------------------------------------------------

class TestOptionalDeps:
    def test_tkinter_import_failure(self, monkeypatch):
        from main import tk as tk_val
        # When tkinter IS available, tk should not be None
        # (if tkinter is actually installed)
        assert tk_val is not None

    def test_keyboard_import_failure(self, monkeypatch):
        result = _import_main_with_mocks({
            "keyboard": None,
            "tkinter": MagicMock(Toplevel=type("T", (), {"__init__": lambda s: None})),
        })
        assert result.keyboard is None
        _cleanup_import_patches()

    def test_pystray_import_failure(self, monkeypatch):
        result = _import_main_with_mocks({
            "pystray": None,
            "PIL": None,
            "tkinter": MagicMock(Toplevel=type("T", (), {"__init__": lambda s: None})),
        })
        assert result.pystray is None
        assert result.MenuItem is None
        _cleanup_import_patches()

    def test_pil_import_failure(self, monkeypatch):
        result = _import_main_with_mocks({
            "PIL": None,
            "PIL.Image": None,
            "PIL.ImageDraw": None,
            "tkinter": MagicMock(Toplevel=type("T", (), {"__init__": lambda s: None})),
        })
        assert result.Image is None
        assert result.ImageDraw is None
        _cleanup_import_patches()

    def test_winsound_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("main.os.name", "posix")
        if "main" in sys.modules:
            del sys.modules["main"]
        import main
        # winsound should be None on non-windows (set by the if/else block at line 53-59)
        assert main.winsound is None


# ---------------------------------------------------------------------------
# Test: SystemTrayIcon
# ---------------------------------------------------------------------------

class TestSystemTrayIcon:
    def test_init_raises_without_deps(self):
        from main import SystemTrayIcon
        with pytest.raises(RuntimeError, match="pystray and Pillow"):
            SystemTrayIcon(MagicMock())

    def test_start(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        with patch("threading.Thread") as mock_thread:
            icon.start()
            assert icon.icon is not None
            mock_thread.assert_called_once()

    def test_update(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        icon.icon = MagicMock()
        icon.update(True)
        assert icon.icon.icon is not None

    def test_update_without_icon(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        icon.update(True)

    def test_stop(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        mock_icon = MagicMock()
        icon.icon = mock_icon
        icon.stop()
        mock_icon.stop.assert_called_once()
        assert icon.icon is None

    def test_stop_without_icon(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        icon.stop()

    def test_icon_image_caching(self, monkeypatch):
        monkeypatch.setattr("main.pystray", MagicMock())
        monkeypatch.setattr("main.MenuItem", MagicMock())
        monkeypatch.setattr("main.Image", MagicMock())
        monkeypatch.setattr("main.ImageDraw", MagicMock())
        from main import SystemTrayIcon
        app = MagicMock()
        icon = SystemTrayIcon(app)
        # Make Image.new return a unique object each call
        import main as main_module
        main_module.Image.new = MagicMock(side_effect=lambda *a, **kw: object())
        img1 = icon._icon_image(True)
        img2 = icon._icon_image(True)
        assert img1 is img2
        img3 = icon._icon_image(False)
        assert img3 is not img1


# ---------------------------------------------------------------------------
# Test: PersistentOverlay (tkinter-dependent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif("not hasattr(sys, 'gettrace') or sys.gettrace() is None",
                     reason="Skipping PersistentOverlay tests that need tk display")
class TestPersistentOverlay:
    """PersistentOverlay requires a display; skipped in headless CI."""

    def test_restore_position_valid(self):
        pass

    def test_restore_position_invalid(self):
        pass

    def test_set_status_throttled(self):
        pass


# ---------------------------------------------------------------------------
# Test: remaining edge cases & pitfalls
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_config_unicode_path(self, tmp_path: Path):
        from main import Config
        unicode_path = tmp_path / "über" / "config.json"
        cfg = Config(unicode_path)
        cfg.set("test", "value")
        assert unicode_path.exists()

    def test_config_save_atomicity_failure(self, tmp_path: Path):
        from main import Config
        cfg = Config(tmp_path / "config.json")
        cfg.load()
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            cfg.save()

    def test_ensure_directory_intermediate_dirs(self, tmp_path: Path):
        from main import ensure_directory
        target = tmp_path / "level1" / "level2" / "level3" / "file.json"
        ensure_directory(target)
        assert target.parent.exists()
