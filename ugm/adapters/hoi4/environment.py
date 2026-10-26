"""
Universal Game MCP (UGM) - Real Windows Environment & HOI4 Detection.
Discovers Windows version, Python runtime, Steam library installations,
HOI4 executable and version, running process state, and OCR availability.
Completely safe: read-only inspection, never launches or controls unapproved processes.
"""

from __future__ import annotations
import os
import sys
import platform
import json
import logging
from typing import Dict, List, Optional, Any
import psutil

logger = logging.getLogger("ugm.adapter.hoi4.env")

COMMON_HOI4_PATHS = [
    r"F:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    r"C:\SteamLibrary\steamapps\common\Hearts of Iron IV",
]


class HOI4EnvironmentDetector:
    """Safe Windows environment and game installation inspector."""

    def detect(self, custom_game_path: Optional[str] = None) -> Dict[str, Any]:
        """Convenience instance method delegating to detect_environment."""
        return self.detect_environment(custom_game_path)

    def discover_installations(self) -> List[Dict[str, Any]]:
        """Returns list of discovered game installations with path and version."""
        env = self.detect_environment()
        if env.get("hoi4_installed") and env.get("game_path"):
            return [{
                "install_dir": env.get("game_path"),
                "exe_path": env.get("executable_path"),
                "version": env.get("game_version")
            }]
        return []

    @staticmethod
    def detect_environment(custom_game_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Gathers comprehensive environment details.
        """
        # 1. OS & Python
        win_ver = platform.platform()
        py_ver = sys.version.split()[0]

        # 2. Game Installation Detection
        game_path = None
        exe_path = None
        game_version = None

        candidate_paths = []
        if custom_game_path:
            candidate_paths.append(custom_game_path)
        candidate_paths.extend(COMMON_HOI4_PATHS)

        # Also search Steam libraryfolders.vdf if available
        steam_libs = HOI4EnvironmentDetector._find_steam_libraries()
        for lib in steam_libs:
            p = os.path.join(lib, "steamapps", "common", "Hearts of Iron IV")
            if p not in candidate_paths:
                candidate_paths.append(p)

        for p in candidate_paths:
            if os.path.isdir(p):
                possible_exe = os.path.join(p, "hoi4.exe")
                if os.path.isfile(possible_exe):
                    game_path = os.path.abspath(p)
                    exe_path = os.path.abspath(possible_exe)
                    game_version = HOI4EnvironmentDetector._read_version(game_path)
                    break

        # 3. Running process detection
        is_running = False
        running_pid = None
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name", "")
                if name and "hoi4" in name.lower():
                    is_running = True
                    running_pid = proc.info.get("pid")
                    break
            except Exception:
                pass

        # 4. OCR Availability
        rapidocr_available = False
        try:
            import rapidocr_onnxruntime
            rapidocr_available = True
        except ImportError:
            pass

        winocr_available = False
        if sys.platform == "win32":
            try:
                import winsdk.windows.media.ocr as _ocr
                winocr_available = True
            except Exception:
                pass

        return {
            "os": win_ver,
            "python_version": py_ver,
            "hoi4_installed": game_path is not None,
            "game_path": game_path,
            "executable_path": exe_path,
            "game_version": game_version,
            "is_running": is_running,
            "running_pid": running_pid,
            "ocr_backends": {
                "rapidocr": rapidocr_available,
                "winocr": winocr_available
            },
            "offline_mode_recommended": not is_running
        }

    @staticmethod
    def _read_version(game_dir: str) -> Optional[str]:
        """Reads game version from launcher-settings.json or changelog.txt."""
        settings_file = os.path.join(game_dir, "launcher-settings.json")
        if os.path.isfile(settings_file):
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("version") or data.get("rawVersion")
            except Exception:
                pass
        return None

    @staticmethod
    def _find_steam_libraries() -> List[str]:
        """Discovers additional Steam library folders from registry or config."""
        libs = []
        if sys.platform != "win32":
            return libs
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            winreg.CloseKey(key)
            if steam_path and os.path.isdir(steam_path):
                libs.append(os.path.normpath(steam_path))
                vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
                if os.path.isfile(vdf):
                    with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if '"path"' in line.lower():
                                parts = line.split('"')
                                if len(parts) >= 4 and os.path.isdir(parts[3]):
                                    libs.append(os.path.normpath(parts[3]))
        except Exception:
            pass
        return libs
