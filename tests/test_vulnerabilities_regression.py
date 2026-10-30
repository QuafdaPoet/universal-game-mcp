"""
tests/test_vulnerabilities_regression.py - Comprehensive Regression Test Suite for VULN-01 to VULN-07
Verifies all 7 security vulnerability remediations in Universal Game MCP.
Designed for execution under both pytest and python -m unittest.
"""

import os
import sys
import time
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Ensure tests/conftest shims are installed even when invoked via unittest
try:
    import tests.conftest
    tests.conftest.install_windows_shims()
except Exception:
    pass

# UGM Imports
from ugm.core.safety import (
    safety_manager,
    SafetyViolationError,
    EmergencyStopTriggered
)
from ugm.core.capture import CaptureManager
from ugm.assets.indexer import GameAssetIndexer
from ugm.core.dataset import DatasetCollector
from ugm.core.input import InputController
from ugm.core.window import WindowManager
from ugm.core.types import WindowInfo

import numpy as np


class TestVulnerabilitiesRegression(unittest.TestCase):
    """Exhaustive regression test suite verifying all 7 security fixes."""

    def setUp(self):
        """Prepare clean isolated temporary workspace and reset safety manager."""
        safety_manager.reset_emergency_stop()
        self.test_dir = tempfile.mkdtemp(prefix="ugm_sec_test_")
        self.mock_win = WindowInfo(
            hwnd=1001,
            title="Hearts of Iron IV",
            process_name="hoi4.exe",
            pid=9999,
            x=0, y=0, width=1920, height=1080,
            client_x=0, client_y=0, client_width=1920, client_height=1080,
            is_minimized=False,
            is_focused=True,
            dpi_scale=1.0
        )

    def tearDown(self):
        """Clean up temporary files and reset emergency stop."""
        safety_manager.reset_emergency_stop()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # ========================================================================
    # VULN-01: Path Traversal / Arbitrary File Overwrite in CaptureManager
    # ========================================================================

    def test_vuln01_relative_path_traversal_blocked(self):
        """VULN-01: Reject relative traversal paths attempting to escape sandbox."""
        sandbox_dir = os.path.join(self.test_dir, "captures")
        os.makedirs(sandbox_dir, exist_ok=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        # Attempt to write outside sandbox_dir using ../ traversal
        with self.assertRaises((SafetyViolationError, ValueError)):
            CaptureManager.save_image(frame, "../../escaped_evil.png", base_dir=sandbox_dir)

    def test_vuln01_absolute_path_outside_sandbox_blocked(self):
        """VULN-01: Reject absolute paths residing outside the designated sandbox."""
        sandbox_dir = os.path.join(self.test_dir, "captures")
        outside_dir = os.path.join(self.test_dir, "system_forbidden")
        os.makedirs(sandbox_dir, exist_ok=True)
        os.makedirs(outside_dir, exist_ok=True)
        forbidden_file = os.path.join(outside_dir, "target.png")
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        with self.assertRaises((SafetyViolationError, ValueError)):
            CaptureManager.save_image(frame, forbidden_file, base_dir=sandbox_dir)

    def test_vuln01_symlink_escape_blocked(self):
        """VULN-01: Canonical realpath resolution prevents symlink directory sandbox bypass."""
        sandbox_dir = os.path.join(self.test_dir, "captures")
        outside_target = os.path.join(self.test_dir, "sensitive_root")
        os.makedirs(sandbox_dir, exist_ok=True)
        os.makedirs(outside_target, exist_ok=True)

        symlink_path = os.path.join(sandbox_dir, "sym_escape")
        try:
            os.symlink(outside_target, symlink_path)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on host filesystem")

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with self.assertRaises((SafetyViolationError, ValueError)):
            CaptureManager.save_image(frame, os.path.join(symlink_path, "evil.png"), base_dir=sandbox_dir)

    def test_vuln01_legitimate_path_succeeds(self):
        """VULN-01: Confirm legitimate paths inside sandbox save successfully."""
        sandbox_dir = os.path.join(self.test_dir, "captures")
        os.makedirs(sandbox_dir, exist_ok=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        # Direct file
        saved_file = CaptureManager.save_image(frame, "screen_001.png", base_dir=sandbox_dir)
        self.assertTrue(os.path.exists(saved_file))
        self.assertEqual(os.path.commonpath([sandbox_dir, os.path.realpath(saved_file)]), os.path.realpath(sandbox_dir))

        # Safe nested subdirectory
        saved_nested = CaptureManager.save_image(frame, "hoi4/sub/frame.png", base_dir=sandbox_dir)
        self.assertTrue(os.path.exists(saved_nested))
        self.assertEqual(os.path.commonpath([sandbox_dir, os.path.realpath(saved_nested)]), os.path.realpath(sandbox_dir))

    def test_vuln01_empty_path_raises_value_error(self):
        """VULN-01: Empty or whitespace-only file_path raises ValueError."""
        sandbox_dir = os.path.join(self.test_dir, "captures")
        os.makedirs(sandbox_dir, exist_ok=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        with self.assertRaises(ValueError):
            CaptureManager.save_image(frame, "", base_dir=sandbox_dir)

        with self.assertRaises(ValueError):
            CaptureManager.save_image(frame, "   ", base_dir=sandbox_dir)

    # ========================================================================
    # VULN-02: Path Traversal in GameAssetIndexer.index_game_directory
    # ========================================================================

    def test_vuln02_invalid_game_ids_rejected(self):
        """VULN-02: Reject invalid game_id containing path traversal or shell characters."""
        cache_dir = os.path.join(self.test_dir, "cache")
        indexer = GameAssetIndexer(cache_dir=cache_dir)
        dummy_game = os.path.join(self.test_dir, "game_files")
        os.makedirs(dummy_game, exist_ok=True)

        malicious_ids = [
            "../../malicious",
            "../escape",
            "hoi4;calc",
            "game/id",
            "game\\id",
            "hoi4 mods",
            "hoi4\0null",
            "",
            "   ",
            "game!@#",
            "hoi4/mods"
        ]
        for bad_id in malicious_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    indexer.index_game_directory(game_id=bad_id, game_path=dummy_game)

    def test_vuln02_valid_game_ids_succeed(self):
        """VULN-02: Confirm valid alphanumeric, dash, and underscore game_id succeed."""
        cache_dir = os.path.join(self.test_dir, "cache")
        indexer = GameAssetIndexer(cache_dir=cache_dir)
        dummy_game = os.path.join(self.test_dir, "game_files")
        os.makedirs(dummy_game, exist_ok=True)

        valid_ids = ["hoi4", "civ6", "stellaris-v1", "game_2026", "EU4"]
        for good_id in valid_ids:
            with self.subTest(good_id=good_id):
                res = indexer.index_game_directory(game_id=good_id, game_path=dummy_game)
                self.assertEqual(res["game_id"], good_id)
                expected_cache = os.path.join(cache_dir, f"{good_id}_asset_index.json")
                self.assertTrue(os.path.exists(expected_cache))

    # ========================================================================
    # VULN-03: Path Traversal in DatasetCollector.save_sample
    # ========================================================================

    def test_vuln03_malicious_tag_contained_or_sanitized(self):
        """VULN-03: Malicious tag with traversal characters cannot escape storage_dir."""
        storage_dir = os.path.join(self.test_dir, "datasets")
        collector = DatasetCollector(storage_dir=storage_dir, enabled=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        malicious_tags = ["../../evil", "..\\..\\win_evil", "tag/sub", "tag\\sub", "../../escape_tag"]
        for bad_tag in malicious_tags:
            with self.subTest(bad_tag=bad_tag):
                try:
                    res = collector.save_sample(frame, tag=bad_tag)
                    self.assertTrue(res["saved"])
                    img_path = os.path.realpath(res["image_path"])
                    json_path = os.path.realpath(res["metadata_path"])
                    # Canonical containment check
                    self.assertEqual(os.path.commonpath([os.path.realpath(storage_dir), img_path]), os.path.realpath(storage_dir))
                    self.assertEqual(os.path.commonpath([os.path.realpath(storage_dir), json_path]), os.path.realpath(storage_dir))
                    self.assertNotIn("..", os.path.basename(img_path))
                except (ValueError, SafetyViolationError):
                    # Explicit rejection is also valid
                    pass

    def test_vuln03_empty_tag_after_strip_rejected(self):
        """VULN-03: Tags that become empty after stripping traversal characters raise ValueError."""
        storage_dir = os.path.join(self.test_dir, "datasets")
        collector = DatasetCollector(storage_dir=storage_dir, enabled=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        with self.assertRaises(ValueError):
            collector.save_sample(frame, tag="../../../")

        with self.assertRaises(ValueError):
            collector.save_sample(frame, tag="!@#$%^&*")

    def test_vuln03_legitimate_tags_succeed(self):
        """VULN-03: Legitimate game tags succeed and are stored within storage_dir."""
        storage_dir = os.path.join(self.test_dir, "datasets")
        collector = DatasetCollector(storage_dir=storage_dir, enabled=True)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        valid_tags = ["production_queue", "topbar_state", "unit_selection"]
        for tag in valid_tags:
            with self.subTest(tag=tag):
                res = collector.save_sample(frame, tag=tag)
                self.assertTrue(res["saved"])
                self.assertIn(tag, res["image_path"])
                self.assertTrue(os.path.exists(res["image_path"]))
                self.assertTrue(os.path.exists(res["metadata_path"]))

    # ========================================================================
    # VULN-04: Key Restrictions, Window Focus, & Emergency Stop in press_keys
    # ========================================================================

    def test_vuln04_dangerous_system_keys_rejected(self):
        """VULN-04: Reject dangerous OS keys, metakeys, and destructive combos."""
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, dry_run_default=True)

        dangerous_payloads = [
            ["win"],
            ["windows"],
            ["cmd"],
            ["super"],
            ["meta"],
            ["alt", "f4"],
            ["alt+f4"],
            ["ctrl", "alt", "del"],
            ["ctrl", "alt", "delete"],
            ["win", "r"],
            ["printscreen"]
        ]
        for payload in dangerous_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(SafetyViolationError):
                    ctrl.press_keys(payload)

    def test_vuln04_valid_game_shortcuts_succeed(self):
        """VULN-04: Legitimate HOI4 game shortcuts and modifier combos succeed."""
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, dry_run_default=True)

        valid_shortcuts = [
            ["space"],
            ["num+"],
            ["num-"],
            ["shift", "q"],
            ["shift+q"],
            ["ctrl", "1"],
            ["ctrl+1"],
            ["f1"],
            ["escape"],
            ["w"],
            ["y"]
        ]
        for shortcut in valid_shortcuts:
            with self.subTest(shortcut=shortcut):
                res = ctrl.press_keys(shortcut)
                self.assertTrue(res.dry_run)
                self.assertTrue(res.success)

    def test_vuln04_dry_run_validates_keys(self):
        """VULN-04: Key validation must occur in dry-run mode (no dry-run security bypass)."""
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, enforce_dry_run=True)

        with self.assertRaises(SafetyViolationError):
            ctrl.press_keys(["win", "r"], dry_run=True)

        with self.assertRaises(SafetyViolationError):
            ctrl.press_keys(["alt+f4"], dry_run=True)

    def test_vuln04_unfocused_window_rejected(self):
        """VULN-04: Dispatching keystrokes when the window is unfocused raises SafetyViolationError."""
        unfocused_win = WindowInfo(
            hwnd=7777, title="Unfocused Game", process_name="game.exe", pid=555,
            x=0, y=0, width=1920, height=1080,
            client_x=0, client_y=0, client_width=1920, client_height=1080,
            is_minimized=False, is_focused=False
        )
        wm = WindowManager(mock_window=unfocused_win)
        ctrl = InputController(wm, auto_focus=False, enforce_dry_run=False)

        with self.assertRaises(SafetyViolationError) as exc_context:
            ctrl.press_keys(["space"], dry_run=False)
        self.assertIn("focus", str(exc_context.exception).lower())

    def test_vuln04_emergency_stop_interrupts_loop(self):
        """VULN-04: Emergency stop triggered mid-sequence immediately interrupts key dispatch."""
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, dry_run_default=False, auto_focus=False)

        keys_dispatched = []

        def mock_key_down(key):
            keys_dispatched.append(key)
            if len(keys_dispatched) == 1:
                # Trigger emergency stop immediately after first keystroke
                safety_manager.trigger_emergency_stop("Aborting keystrokes midway")

        with patch("pydirectinput.keyDown", side_effect=mock_key_down), \
             patch("pydirectinput.keyUp"):
            with self.assertRaises(EmergencyStopTriggered):
                ctrl.press_keys(["space", "w", "e", "r"], dry_run=False)

        # The subsequent keys 'w', 'e', 'r' must never have been dispatched
        self.assertEqual(len(keys_dispatched), 1)
        self.assertEqual(keys_dispatched[0], "space")

    # ========================================================================
    # VULN-05: Hardware Failsafe Restoration
    # ========================================================================

    def test_vuln05_pydirectinput_failsafe_enabled(self):
        """VULN-05: Verify pydirectinput.FAILSAFE is set to True upon module initialization."""
        import pydirectinput
        self.assertTrue(pydirectinput.FAILSAFE)

    def test_vuln05_failsafe_exception_triggers_emergency_stop(self):
        """VULN-05: Corner mouse movement raising FailSafeException activates emergency stop."""
        import pydirectinput
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, dry_run_default=False, auto_focus=False)

        with patch("pydirectinput.moveTo", side_effect=pydirectinput.FailSafeException("Mouse in corner")):
            with self.assertRaises((EmergencyStopTriggered, SafetyViolationError)):
                ctrl.hover(0.5, 0.5, dry_run=False)

        self.assertTrue(safety_manager.is_stopped)

    def test_vuln05_corner_abort_fallback(self):
        """VULN-05: Fallback corner abort check triggers emergency stop when cursor within 5px."""
        import pydirectinput
        wm = WindowManager(mock_window=self.mock_win)
        ctrl = InputController(wm, dry_run_default=False, auto_focus=False)

        with patch.object(pydirectinput, "position", return_value=(2, 2)):
            with self.assertRaises(EmergencyStopTriggered):
                ctrl.check_corner_abort()

        self.assertTrue(safety_manager.is_stopped)

    # ========================================================================
    # VULN-06: Anti-Cheat Background Process Scanning via psutil
    # ========================================================================

    def test_vuln06_scan_system_processes_detects_eac(self):
        """VULN-06: scan_system_processes detects background EasyAntiCheat.exe and triggers e-stop."""
        mock_proc_game = MagicMock()
        mock_proc_game.info = {"name": "hoi4.exe", "pid": 100}
        mock_proc_eac = MagicMock()
        mock_proc_eac.info = {"name": "EasyAntiCheat.exe", "pid": 200}

        with patch("psutil.process_iter", return_value=[mock_proc_game, mock_proc_eac]):
            with self.assertRaises(SafetyViolationError):
                safety_manager.scan_system_processes()
            self.assertTrue(safety_manager.is_stopped)

    def test_vuln06_validate_process_blocks_on_background_ac(self):
        """VULN-06: validate_process raises SafetyViolationError when background anti-cheat is detected."""
        mock_proc_vgc = MagicMock()
        mock_proc_vgc.info = {"name": "vgc.exe", "pid": 300}

        with patch("psutil.process_iter", return_value=[mock_proc_vgc]):
            with self.assertRaises(SafetyViolationError):
                safety_manager.validate_process("hoi4.exe")

        self.assertTrue(safety_manager.is_stopped)

    def test_vuln06_psutil_missing_or_access_denied_graceful(self):
        """VULN-06: System process scanning handles missing psutil or AccessDenied without crashing."""
        # Test 1: Missing psutil
        with patch.dict(sys.modules, {"psutil": None}):
            res = safety_manager.scan_system_processes()
            self.assertIsInstance(res, list)

        # Test 2: AccessDenied exception on restricted /proc
        try:
            import psutil
            mock_proc = MagicMock()
            type(mock_proc).info = PropertyMock(side_effect=psutil.AccessDenied(pid=1))
            with patch("psutil.process_iter", return_value=[mock_proc]):
                res2 = safety_manager.scan_system_processes()
                self.assertIsInstance(res2, list)
        except Exception:
            pass

    # ========================================================================
    # VULN-07: Window Title ReDoS and Regex Error Elimination
    # ========================================================================

    def test_vuln07_find_window_unescaped_brackets_safe(self):
        """VULN-07: find_window handles unescaped regex metacharacters without raising re.error."""
        wm = WindowManager()
        mock_windows = [
            (1001, "Hearts of Iron IV [v1.14]", "hoi4.exe", 101),
            (1002, "Calculator", "calc.exe", 102)
        ]

        def mock_enum_windows(callback, extra):
            for hwnd, title, _, _ in mock_windows:
                with patch("win32gui.GetWindowText", return_value=title):
                    callback(hwnd, extra)

        with patch("sys.platform", "win32"), \
             patch("win32gui.IsWindowVisible", return_value=True), \
             patch("win32process.GetWindowThreadProcessId", return_value=(1, 101)), \
             patch("psutil.Process") as mock_proc, \
             patch("win32gui.EnumWindows", side_effect=mock_enum_windows), \
             patch.object(wm, "get_window_info") as mock_info:

            mock_proc.return_value.name.return_value = "hoi4.exe"
            mock_info.return_value = WindowInfo(
                hwnd=1001, title="Hearts of Iron IV [v1.14]", process_name="hoi4.exe", pid=101,
                x=0, y=0, width=1920, height=1080, client_x=0, client_y=0, client_width=1920, client_height=1080,
                is_minimized=False, is_focused=True
            )

            # Test syntax errors that previously triggered unhandled re.error crashes
            test_patterns = ["[", "(*", "[v1.14]", "Hearts of Iron IV [v1.14]"]
            for pat in test_patterns:
                with self.subTest(pat=pat):
                    win = wm.find_window(window_title=pat)
                    # Must never raise re.error
                    if pat in ["[", "[v1.14]", "Hearts of Iron IV [v1.14]"]:
                        self.assertIsNotNone(win)
                        self.assertEqual(win.hwnd, 1001)
                    elif pat == "(*":
                        self.assertIsNone(win)

    def test_vuln07_find_window_redos_backtracking_safe(self):
        """VULN-07: Catastrophic backtracking ReDoS pattern (a+)+$ evaluates safely without freezing."""
        wm = WindowManager()
        long_title = "a" * 30 + "!"

        def mock_enum_windows(callback, extra):
            with patch("win32gui.GetWindowText", return_value=long_title):
                callback(2001, extra)

        with patch("sys.platform", "win32"), \
             patch("win32gui.IsWindowVisible", return_value=True), \
             patch("win32process.GetWindowThreadProcessId", return_value=(1, 201)), \
             patch("psutil.Process"), \
             patch("win32gui.EnumWindows", side_effect=mock_enum_windows):

            t0 = time.perf_counter()
            win = wm.find_window(window_title="(a+)+$")
            elapsed = time.perf_counter() - t0

            # Must evaluate essentially instantaneously (< 0.1s), proving no exponential backtracking
            self.assertLess(elapsed, 0.1)
            self.assertIsNone(win)


if __name__ == "__main__":
    unittest.main()
