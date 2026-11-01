"""
Adversarial empirical stress-test suite for Milestone 1 (Bug A & Bug B).
Authored by Challenger 1 (Adversarial Verifier).

Tests:
1. extract_tool_payload with adversarial inputs:
   - is_error=True with various forms of error text, empty content, missing attributes, unicode
   - structured_content=None with valid JSON dict/list/primitives in content[0].text
   - structured_content=None with non-JSON text in content[0].text
   - Nested {"result": {"nested": 123}} vs flat {"found": False}
   - Edge cases: completely empty or invalid result objects
2. UGMContext.setup_offline_fixture:
   - Custom titles
   - Unicode titles
   - None (default fallback to HOI4)
   - Empty string title fallback
   - Non-existent fixture path error handling
3. WindowManager.find_window isolation in mock mode:
   - Safe return of None for non-existent windows
   - Strict guarantee that win32gui.EnumWindows / live OS enumeration is NEVER called
   - Case-insensitive and partial matching behavior
4. Dry-run verify_selection integrity:
   - verify_selection returns verified=False on static fixture
   - Diff ratio calculation behaves correctly with identical and modified frames
   - No false positives reported
"""

import os
import sys
import json
import types
import unittest
from typing import Any, Dict, List
import numpy as np
import cv2

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.demo_offline_sim import extract_tool_payload
from ugm.core.window import WindowManager, WindowInfo
from ugm.core.verifier import ActionVerifier, VerificationResult
from ugm.server import UGMContext, create_server


class MockToolResult:
    """Configurable mock for CallToolResult to test extraction logic."""
    def __init__(
        self,
        is_error: bool = False,
        structured_content: Any = None,
        content: Any = None
    ):
        self.is_error = is_error
        self.structured_content = structured_content
        self.content = content


class TestBugAExtractToolPayloadAdversarial(unittest.TestCase):
    """Adversarial stress-testing of extract_tool_payload."""

    # -------------------------------------------------------------------------
    # 1. is_error=True scenarios
    # -------------------------------------------------------------------------
    def test_error_with_standard_text_content(self):
        item = types.SimpleNamespace(text="Invalid coordinates provided")
        res = MockToolResult(is_error=True, content=[item])
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "hover_target")
        self.assertIn("Invalid coordinates provided", str(ctx.exception))
        self.assertIn("hover_target", str(ctx.exception))

    def test_error_with_empty_content_list(self):
        res = MockToolResult(is_error=True, content=[])
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "click_target")
        self.assertIn("Unknown error", str(ctx.exception))

    def test_error_with_none_content(self):
        res = MockToolResult(is_error=True, content=None)
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "press_keys")
        self.assertIn("Unknown error", str(ctx.exception))

    def test_error_without_content_attribute(self):
        class BareResult:
            is_error = True
        res = BareResult()
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "emergency_stop")
        self.assertIn("Unknown error", str(ctx.exception))

    def test_error_with_content_item_missing_text_attribute(self):
        item = types.SimpleNamespace(data="binary blob")
        res = MockToolResult(is_error=True, content=[item])
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "capture_screen")
        self.assertIn("Unknown error", str(ctx.exception))

    def test_error_with_unicode_and_emojis(self):
        err_text = "致命的なエラー: 窗口不存在 💥 🚫"
        item = types.SimpleNamespace(text=err_text)
        res = MockToolResult(is_error=True, content=[item])
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "get_game_window")
        self.assertIn(err_text, str(ctx.exception))

    def test_error_with_multi_element_content(self):
        items = [
            types.SimpleNamespace(text="Primary error message"),
            types.SimpleNamespace(text="Secondary debug detail")
        ]
        res = MockToolResult(is_error=True, content=items)
        with self.assertRaises(RuntimeError) as ctx:
            extract_tool_payload(res, "read_text")
        self.assertIn("Primary error message", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 2. structured_content=None with JSON text in content[0].text
    # -------------------------------------------------------------------------
    def test_json_dict_in_content_text(self):
        raw_json = json.dumps({"status": "active", "counter": 42})
        item = types.SimpleNamespace(text=raw_json)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_json_dict")
        self.assertEqual(payload, {"status": "active", "counter": 42})

    def test_json_list_in_content_text(self):
        raw_json = json.dumps([{"id": 1}, {"id": 2}])
        item = types.SimpleNamespace(text=raw_json)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_json_list")
        self.assertEqual(payload, [{"id": 1}, {"id": 2}])

    def test_json_wrapped_result_in_content_text(self):
        raw_json = json.dumps({"result": {"nested_key": 999}})
        item = types.SimpleNamespace(text=raw_json)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_wrapped")
        # Should unwrap single key "result"
        self.assertEqual(payload, {"nested_key": 999})

    def test_json_primitives_in_content_text(self):
        for primitive in ["plain string", 12345, True, None]:
            raw_json = json.dumps(primitive)
            item = types.SimpleNamespace(text=raw_json)
            res = MockToolResult(is_error=False, structured_content=None, content=[item])
            payload = extract_tool_payload(res, "tool_primitive")
            self.assertEqual(payload, primitive)

    # -------------------------------------------------------------------------
    # 3. structured_content=None with non-JSON text in content[0].text
    # -------------------------------------------------------------------------
    def test_non_json_plain_text(self):
        raw_text = "502 Bad Gateway: upstream server unavailable"
        item = types.SimpleNamespace(text=raw_text)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_non_json")
        self.assertEqual(payload, {"text": raw_text})

    def test_non_json_malformed_brackets(self):
        raw_text = "{key: value without quotes"
        item = types.SimpleNamespace(text=raw_text)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_malformed")
        self.assertEqual(payload, {"text": raw_text})

    def test_non_json_empty_text(self):
        raw_text = ""
        item = types.SimpleNamespace(text=raw_text)
        res = MockToolResult(is_error=False, structured_content=None, content=[item])
        payload = extract_tool_payload(res, "tool_empty_text")
        self.assertEqual(payload, {"text": ""})

    # -------------------------------------------------------------------------
    # 4. Nested {"result": ...} vs flat {"found": False}
    # -------------------------------------------------------------------------
    def test_nested_result_dict_unwrapping(self):
        res = MockToolResult(
            is_error=False,
            structured_content={"result": {"nested": 123, "active": True}}
        )
        payload = extract_tool_payload(res, "tool_nested")
        self.assertEqual(payload, {"nested": 123, "active": True})

    def test_nested_result_list_unwrapping(self):
        res = MockToolResult(
            is_error=False,
            structured_content={"result": ["item1", "item2"]}
        )
        payload = extract_tool_payload(res, "tool_nested_list")
        self.assertEqual(payload, ["item1", "item2"])

    def test_flat_result_dict_not_unwrapped(self):
        res = MockToolResult(
            is_error=False,
            structured_content={"found": False, "window": None}
        )
        payload = extract_tool_payload(res, "tool_flat")
        self.assertEqual(payload, {"found": False, "window": None})

    def test_result_dict_with_multiple_keys(self):
        # When "result" is accompanied by another key (e.g. "meta"), do NOT unwrap
        res = MockToolResult(
            is_error=False,
            structured_content={"result": {"nested": 123}, "status": "ok"}
        )
        payload = extract_tool_payload(res, "tool_multi_key")
        self.assertEqual(payload, {"result": {"nested": 123}, "status": "ok"})

    def test_result_dict_with_primitive_value(self):
        # Single key "result" with primitive string or int should not fail type check
        res = MockToolResult(
            is_error=False,
            structured_content={"result": "simple_string"}
        )
        payload = extract_tool_payload(res, "tool_primitive_val")
        self.assertEqual(payload, {"result": "simple_string"})

    # -------------------------------------------------------------------------
    # 5. Empty / Corrupted Tool Results
    # -------------------------------------------------------------------------
    def test_empty_result_raises_value_error(self):
        res = MockToolResult(is_error=False, structured_content=None, content=[])
        with self.assertRaises(ValueError) as ctx:
            extract_tool_payload(res, "corrupt_tool")
        self.assertIn("unparseable or empty payload", str(ctx.exception))


class TestBugBWindowAndFixtureAdversarial(unittest.TestCase):
    """Adversarial stress-testing of UGMContext.setup_offline_fixture and WindowManager."""

    def setUp(self):
        self.fixture_path = "tests/fixtures/sim_fixture.png"
        if not os.path.exists(self.fixture_path):
            os.makedirs("tests/fixtures", exist_ok=True)
            img = np.zeros((100, 100, 3), dtype=np.uint8)
            cv2.imwrite(self.fixture_path, img)

    def test_setup_offline_fixture_custom_title(self):
        ctx = UGMContext()
        ctx.setup_offline_fixture(
            self.fixture_path,
            title="Custom Simulator Alpha",
            process_name="custom_sim.exe"
        )
        win = ctx.window_manager.current_window
        self.assertIsNotNone(win)
        self.assertEqual(win.title, "Custom Simulator Alpha")
        self.assertEqual(win.process_name, "custom_sim.exe")

    def test_setup_offline_fixture_unicode_title(self):
        unicode_title = "🎮 Chiến Tranh 2026: 终极版"
        ctx = UGMContext()
        ctx.setup_offline_fixture(
            self.fixture_path,
            title=unicode_title,
            process_name="unicode_sim.exe"
        )
        win = ctx.window_manager.current_window
        self.assertIsNotNone(win)
        self.assertEqual(win.title, unicode_title)

        # Verify substring matching works with unicode
        matched = ctx.window_manager.find_window(window_title="Chiến Tranh")
        self.assertIsNotNone(matched)
        self.assertEqual(matched.title, unicode_title)

        matched_cjk = ctx.window_manager.find_window(window_title="终极版")
        self.assertIsNotNone(matched_cjk)
        self.assertEqual(matched_cjk.title, unicode_title)

    def test_setup_offline_fixture_none_defaults_to_hoi4(self):
        ctx = UGMContext()
        ctx.setup_offline_fixture(self.fixture_path, title=None, process_name=None)
        win = ctx.window_manager.current_window
        self.assertIsNotNone(win)
        self.assertEqual(win.title, "Hearts of Iron IV (Mock Fixture)")
        self.assertEqual(win.process_name, "hoi4.exe")

    def test_setup_offline_fixture_empty_string_defaults_to_hoi4(self):
        ctx = UGMContext()
        ctx.setup_offline_fixture(self.fixture_path, title="", process_name="")
        win = ctx.window_manager.current_window
        self.assertIsNotNone(win)
        self.assertEqual(win.title, "Hearts of Iron IV (Mock Fixture)")
        self.assertEqual(win.process_name, "hoi4.exe")

    def test_setup_offline_fixture_invalid_path_raises(self):
        ctx = UGMContext()
        with self.assertRaises(ValueError):
            ctx.setup_offline_fixture("tests/fixtures/non_existent_image_file.png")

    # -------------------------------------------------------------------------
    # WindowManager Mock Isolation & Zero Live OS Fallthrough
    # -------------------------------------------------------------------------
    def test_find_window_mock_mode_safe_none_on_missing_window(self):
        wm = WindowManager()
        mock_win = WindowInfo(
            hwnd=9999,
            title="UGM Offline Strategy Simulator",
            process_name="offline_sim.exe",
            pid=1234, x=0, y=0, width=900, height=550,
            client_x=0, client_y=0, client_width=900, client_height=550,
            is_minimized=False, is_focused=True
        )
        wm.set_mock_window(mock_win)

        # Completely mismatching queries
        self.assertIsNone(wm.find_window(window_title="Nonexistent Title"))
        self.assertIsNone(wm.find_window(process_name="nonexistent.exe"))
        self.assertIsNone(wm.find_window(window_title="UGM Offline Strategy Simulator", process_name="wrong.exe"))
        self.assertIsNone(wm.find_window(window_title="Wrong Title", process_name="offline_sim.exe"))

    def test_find_window_mock_mode_strictly_avoids_live_os_enumeration(self):
        """
        Adversarial proof: Even on Windows with win32gui available,
        when mock window is configured, WindowManager.find_window MUST NEVER
        call win32gui.EnumWindows or touch live OS processes.
        """
        wm = WindowManager()
        mock_win = WindowInfo(
            hwnd=9999,
            title="UGM Offline Strategy Simulator",
            process_name="offline_sim.exe",
            pid=1234, x=0, y=0, width=900, height=550,
            client_x=0, client_y=0, client_width=900, client_height=550,
            is_minimized=False, is_focused=True
        )
        wm.set_mock_window(mock_win)

        enum_called = False
        def forbidden_enum_windows(*args, **kwargs):
            nonlocal enum_called
            enum_called = True
            raise AssertionError("VIOLATION: win32gui.EnumWindows was invoked while in mock mode!")

        # Monkeypatch win32gui.EnumWindows if win32gui is imported or in sys.modules
        import sys
        old_win32gui = sys.modules.get("win32gui")
        mock_win32gui = types.ModuleType("win32gui")
        mock_win32gui.EnumWindows = forbidden_enum_windows
        sys.modules["win32gui"] = mock_win32gui

        try:
            # Query non-existent window — must return None without calling EnumWindows
            res = wm.find_window(window_title="Completely Bogus Window Title 999")
            self.assertIsNone(res)
            self.assertFalse(enum_called, "win32gui.EnumWindows was illegally invoked!")

            # Query matching window
            res_match = wm.find_window(window_title="Strategy Simulator")
            self.assertIsNotNone(res_match)
            self.assertEqual(res_match.title, "UGM Offline Strategy Simulator")
            self.assertFalse(enum_called, "win32gui.EnumWindows was illegally invoked!")
        finally:
            if old_win32gui is not None:
                sys.modules["win32gui"] = old_win32gui
            else:
                sys.modules.pop("win32gui", None)


class TestDryRunVerifySelectionIntegrity(unittest.TestCase):
    """
    Verifies that dry-run verify_selection=False is NEVER mistakenly
    evaluated or reported as True.
    """

    def test_verify_selection_on_identical_frames_yields_false(self):
        """When pre and post frames are identical (as in a dry-run), verified MUST be False."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.putText(img, "TEST SCREEN", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Mock dependencies for ActionVerifier
        verifier = ActionVerifier(
            capture_manager=None,
            ocr_engine=None,
            input_controller=None
        )

        res: VerificationResult = verifier.verify_selection(
            pre_frame=img,
            post_frame=img.copy(),
            min_diff_ratio=0.005
        )

        self.assertFalse(res.verified, "Dry-run with identical frames must NOT be verified!")
        self.assertEqual(res.diff_ratio, 0.0)

    def test_verify_selection_on_altered_frame_yields_true(self):
        """When an actual visual change occurs, verified is True (proving the verifier is active)."""
        pre_img = np.zeros((200, 200, 3), dtype=np.uint8)
        post_img = pre_img.copy()
        cv2.rectangle(post_img, (10, 10), (100, 100), (255, 255, 255), -1)

        verifier = ActionVerifier(
            capture_manager=None,
            ocr_engine=None,
            input_controller=None
        )

        res: VerificationResult = verifier.verify_selection(
            pre_frame=pre_img,
            post_frame=post_img,
            min_diff_ratio=0.005
        )

        self.assertTrue(res.verified)
        self.assertGreater(res.diff_ratio, 0.005)

    def test_demo_script_dry_run_reporting_contract(self):
        """
        Verify that scripts/demo_offline_sim.py does not assert verified is True
        and explicitly documents expected False for dry-runs.
        """
        with open("scripts/demo_offline_sim.py", "r", encoding="utf-8") as f:
            code = f.read()

        # Check for forbidden assertion that would fail on dry-runs
        self.assertNotIn("assert ver_data['verified'] is True", code)
        self.assertNotIn("assert ver_data['verified'],", code)

        # Check that dry-run expectation is explicitly annotated in demo output
        self.assertIn("expected False for dry-run on static fixture", code)


if __name__ == "__main__":
    unittest.main()
