"""
Universal Game MCP (UGM) - HOI4 Prebuilt Integration Test Suite
Validates prebuilt campaign knowledge retrieval, environment discovery, advanced perception,
12-state UI recognition, Tier A/B/C skill execution, and end-to-end persistent workflow.
"""

import os
import json
import tempfile
from typing import Optional, Any, Dict, List
import numpy as np
import pytest
import cv2

from ugm.core.capture import FixtureBackend
from ugm.core.ocr import BaseOCREngine
from ugm.core.types import OCRResult, OCRItem, NormalizedRect, BoundingBox
from ugm.adapters.hoi4.adapter import HOI4Adapter
from ugm.adapters.hoi4.knowledge import HOI4KnowledgeManager
from ugm.adapters.hoi4.environment import HOI4EnvironmentDetector
from ugm.adapters.hoi4.perception import (
    parse_hoi4_date,
    detect_pause_state,
    detect_speed_level,
    detect_tooltip_box,
    detect_popups
)
from ugm.navigation.recognizer import UIStateRecognizer
from ugm.skills.loader import GameSkillPackLoader
from ugm.server import UGMContext, create_server
from ugm.navigation.graph import NavigationGraph


def make_ocr_item(text: str, conf: float = 0.95) -> OCRItem:
    return OCRItem(
        text=text,
        confidence=conf,
        bbox_pixel=BoundingBox(0, 0, 100, 20),
        bbox_normalized=NormalizedRect(0.0, 0.0, 0.1, 0.02)
    )


def _res(text: str, items: Optional[List[OCRItem]] = None) -> OCRResult:
    if items is None and text:
        items = [make_ocr_item(text)]
    return OCRResult(
        items=items or [],
        full_text=text,
        engine="mock",
        latency_ms=1.0
    )


class MockOCREngine(BaseOCREngine):
    """Configurable mock OCR engine for controlled UI state testing."""

    def __init__(self, mocked_text: str = ""):
        self.mocked_text = mocked_text

    def read_text(
        self,
        frame: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        event_tag: Optional[str] = None
    ) -> OCRResult:
        txt = self.mocked_text
        lower = txt.lower()

        if event_tag == "state_check_topbar":
            if "single player" in lower or "options" in lower or "select country" in lower or "1936" in lower:
                return _res("")
            return _res(
                "150 100% 85% 1.2M 45 12:00, 1 Jan, 1936",
                [
                    make_ocr_item("150", 0.99),
                    make_ocr_item("100%", 0.99),
                ]
            )
        elif event_tag == "state_check_drawer":
            if "army" in lower or "options" in lower:
                return _res("")
            return _res(txt, [make_ocr_item(txt)])
        elif event_tag == "state_check_focus":
            return _res(txt if "focus" in lower else "", [make_ocr_item(txt)] if "focus" in lower else [])
        elif event_tag == "state_check_center":
            return _res(txt if ("news" in lower or "event" in lower) else "")
        elif event_tag == "state_check_menu":
            return _res(txt, [make_ocr_item(txt)])
        elif event_tag == "state_check_army":
            return _res(txt if "army" in lower else "", [make_ocr_item(txt)] if "army" in lower else [])

        return _res(txt, [make_ocr_item(txt)])


def extract_data(res: Any) -> Dict[str, Any]:
    """Extracts structured payload from MCP tool call response."""
    if hasattr(res, "structured_content") and res.structured_content:
        if "result" in res.structured_content:
            return res.structured_content["result"]
        return res.structured_content
    if hasattr(res, "content") and res.content:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# 1. Prebuilt Campaign Knowledge
# ---------------------------------------------------------------------------

def test_hoi4_knowledge_retrieval():
    km = HOI4KnowledgeManager()

    # Meta
    meta = km.get_knowledge("meta")
    assert meta.get("success") is True
    meta_data = meta.get("data", {})
    assert meta_data.get("game_id") == "hoi4"
    assert "upstream_attribution" in meta_data
    assert meta_data["upstream_attribution"].get("upstream_commit") == "6b91a2a43edfeab60a415ec2ddd9b71d215b8a56"

    # Rules & Modes
    rules = km.get_knowledge("rules_and_modes")
    assert rules.get("success") is True
    rules_data = rules.get("data", {})
    assert "modes" in rules_data
    assert "FAIR" in rules_data["modes"]
    assert "banned_exploits" in rules_data["modes"]["FAIR"]

    # Operating loop
    loop = km.get_knowledge("operating_loop")
    assert loop.get("success") is True
    loop_data = loop.get("data", {})
    assert "operating_cadence" in loop_data
    assert "standing_plan_schema" in loop_data

    # Strategy core
    strat = km.get_knowledge("strategy_core")
    assert strat.get("success") is True
    strat_data = strat.get("data", {})
    assert "economy_and_industry" in strat_data
    assert "production_and_shortages" in strat_data

    # Country guides
    ger = km.get_knowledge("country", "germany_fair")
    assert ger.get("success") is True
    ger_data = ger.get("data", {})
    assert ger_data.get("country_tag") == "GER"
    assert "phases" in ger_data

    chn = km.get_knowledge("country", "china_fair")
    assert chn.get("success") is True
    chn_data = chn.get("data", {})
    assert chn_data.get("country_tag") == "CHI"
    assert "strategic_priorities" in chn_data
    assert "defense_in_depth" in chn_data["strategic_priorities"]

    pol = km.get_knowledge("country", "poland_meta")
    assert pol.get("success") is True
    pol_data = pol.get("data", {})
    assert pol_data.get("country_tag") == "POL"
    assert "meta_mechanics" in pol_data
    assert "romanian_bridgehead" in pol_data["meta_mechanics"]

    # Unknown topic error handling
    unk = km.get_knowledge("unknown_topic")
    assert unk.get("success") is False
    assert "error" in unk
    assert "available_topics" in unk


# ---------------------------------------------------------------------------
# 2. Local Environment Discovery
# ---------------------------------------------------------------------------

def test_hoi4_environment_discovery():
    detector = HOI4EnvironmentDetector()
    env = detector.detect_environment()

    assert "os" in env
    assert "python_version" in env
    assert "hoi4_installed" in env
    assert "is_running" in env
    assert "ocr_backends" in env
    assert env["ocr_backends"].get("rapidocr") is True

    # Real game check (installed on user machine at F:\SteamLibrary)
    if env["hoi4_installed"]:
        assert "Hearts of Iron IV" in env["game_path"]
        assert os.path.isfile(env["executable_path"])
        assert env["game_version"] is not None

    installs = detector.discover_installations()
    assert isinstance(installs, list)


# ---------------------------------------------------------------------------
# 3. Perception Algorithms
# ---------------------------------------------------------------------------

def test_hoi4_perception_date_parsing():
    # Clean OCR string
    res1 = parse_hoi4_date("12:00, 1 Jan, 1936")
    assert res1 is not None
    assert res1["hour"] == 12
    assert res1["day"] == 1
    assert res1["month"] == 1
    assert res1["year"] == 1936
    assert res1["iso"] == "1936-01-01T12:00"

    # OCR digit confusions (l -> 1, O -> 0)
    res2 = parse_hoi4_date("l2:00, l5 Aug, l939")
    assert res2 is not None
    assert res2["hour"] == 12
    assert res2["day"] == 15
    assert res2["month"] == 8
    assert res2["year"] == 1939

    # Invalid string
    assert parse_hoi4_date("Not a date string") is None


def test_hoi4_perception_pause_detection():
    # Blank frame -> unknown
    blank = np.zeros((100, 1000, 3), dtype=np.uint8)
    state, _ = detect_pause_state(blank)
    assert state == "unknown"

    # Frame with green speed icon -> running
    running_frame = np.zeros((100, 1000, 3), dtype=np.uint8)
    running_frame[11:30, 705:723] = [30, 200, 30]
    st_run, _ = detect_pause_state(running_frame)
    assert st_run == "running"

    # Frame with red hazard stripes -> paused
    paused_frame = np.zeros((100, 1000, 3), dtype=np.uint8)
    paused_frame[9:31, 730:860] = [180, 120, 20]
    st_pause, _ = detect_pause_state(paused_frame)
    assert st_pause == "paused"


def test_hoi4_perception_tooltip_box_detection():
    before = np.zeros((500, 500, 3), dtype=np.uint8)
    after = before.copy()

    # Draw synthetic tooltip box in after frame
    cv2.rectangle(after, (100, 100), (250, 160), (220, 220, 220), -1)

    box = detect_tooltip_box(before, after, cursor_xy=(105, 95), min_area=500)
    assert box is not None
    bx, by, bw, bh = box
    assert abs(bx - 100) <= 2
    assert abs(by - 100) <= 2
    assert abs(bw - 150) <= 5
    assert abs(bh - 60) <= 5


def test_hoi4_perception_popup_detection():
    frame = np.zeros((600, 800, 3), dtype=np.uint8)

    # Draw warm parchment country event popup in center
    cv2.rectangle(frame, (200, 150), (600, 450), (210, 170, 110), -1)

    popups = detect_popups(frame)
    assert len(popups) >= 1
    assert popups[0]["kind"] == "event"
    assert popups[0]["box"][2] >= 200


# ---------------------------------------------------------------------------
# 4. 12-State UI Recognition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mock_text,expected_state", [
    ("Single Player Career Profile Options Exit", "main_menu"),
    ("Select Country Interesting Countries 1936 Gathering Storm", "country_selection"),
    ("Military Factories Naval Dockyards Production Lines", "production"),
    ("Civilian Factories Infrastructure Construction Queue", "construction"),
    ("Research Technology Slot Infantry Armor", "research"),
    ("World Tension Faction Diplomacy Non-Aggression", "diplomacy"),
    ("Recruit Deployment Division Designer Training", "recruitment"),
    ("Laws & Government Political Parties Ruling Party", "politics"),
    ("National Focus Tree Select National Focus", "national_focus"),
    ("Army Group 1 Theater 24 Divisions Assigned", "selected_army"),
])
def test_hoi4_ui_state_recognition(mock_text, expected_state):
    ocr = MockOCREngine(mocked_text=mock_text)
    from ugm.core.icon import IconDetector
    recognizer = UIStateRecognizer(ocr_engine=ocr, icon_detector=IconDetector(), storage=None)

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    det = recognizer.recognize_state(frame, game_id="hoi4")

    assert det.is_known
    assert det.state_name == expected_state
    assert det.confidence >= 0.70


# ---------------------------------------------------------------------------
# 5. Prebuilt Game Skills Loading & Schema Conformance
# ---------------------------------------------------------------------------

def test_hoi4_skill_pack_full_conformance():
    pack = GameSkillPackLoader.load_pack("game_skills/hoi4")
    assert pack.game_id == "hoi4"
    assert len(pack.selectors) >= 15
    assert len(pack.skills) >= 25

    # Check Tier A skills
    tier_a = ["pause_unpause", "set_speed", "decrease_speed", "close_dialog", "open_production", "open_research", "open_construction", "open_politics", "read_topbar", "read_tooltip"]
    for s_id in tier_a:
        assert s_id in pack.skills
        skill = pack.skills[s_id]
        assert skill.steps
        assert skill.post_verification

    # Check Tier B skills
    tier_b = ["inspect_research_slots", "start_research", "inspect_national_focus", "select_national_focus", "inspect_production_lines", "adjust_factory_allocation", "inspect_construction_queue", "queue_construction"]
    for s_id in tier_b:
        assert s_id in pack.skills

    # Check Tier C skills
    tier_c = ["list_visible_armies", "select_army", "assign_commander", "inspect_army_orders"]
    for s_id in tier_c:
        assert s_id in pack.skills


# ---------------------------------------------------------------------------
# 6. Server Tools Integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hoi4_server_tools_integration():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_ugm.db")
        ctx = UGMContext(db_path=db_path)
        server = create_server(ctx)

        # 1. hoi4_get_knowledge
        res_kw = await server.call_tool("hoi4_get_knowledge", {"topic": "strategy_core"})
        kw_data = extract_data(res_kw)
        assert kw_data.get("success") is True
        assert "economy_and_industry" in kw_data.get("data", {})

        # 2. hoi4_detect_environment
        res_env = await server.call_tool("hoi4_detect_environment", {})
        env_data = extract_data(res_env)
        assert "hoi4_installed" in env_data

        # 3. index_game_assets with auto-discovery
        res_idx = await server.call_tool("index_game_assets", {"game_id": "hoi4"})
        idx_data = extract_data(res_idx)
        assert idx_data.get("success") is True

        # Close database connection cleanly
        ctx.storage.db.close()


# ---------------------------------------------------------------------------
# 7. Mandatory Multi-Step Demonstration Workflow
# ---------------------------------------------------------------------------

def test_hoi4_mandatory_demonstration_workflow():
    """
    Executes the required full integration flow:
    1. Start at simulated main map.
    2. Navigate to production -> inspect production panel.
    3. Navigate to research -> identify research slot.
    4. Safe selection -> verify state transition.
    5. Restart server context with persistent DB.
    6. Verify persistent knowledge and graph reuse.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "hoi4_persistent_demo.db")

        # Session 1: Initial exploration & state navigation
        ctx1 = UGMContext(db_path=db_path, offline_fixture="tests/fixtures/hoi4_topbar.png")
        ctx1.input_controller.dry_run_default = True

        graph1 = ctx1.get_navigation_graph("hoi4")
        assert "main_map" in graph1.nodes
        assert "production" in graph1.nodes
        assert "research" in graph1.nodes

        # Plan route from main_map to production
        path_to_prod = graph1.plan_route("main_map", "production")
        assert path_to_prod.found
        assert len(path_to_prod.edges) == 1
        assert path_to_prod.edges[0].parameters == {"keys": ["y"]}

        # Plan route from production to research (via main_map: escape -> w)
        path_prod_to_res = graph1.plan_route("production", "research")
        assert path_prod_to_res.found
        assert len(path_prod_to_res.edges) == 2
        assert path_prod_to_res.edges[0].target_node_id == "main_map"
        assert path_prod_to_res.edges[1].target_node_id == "research"

        # Record learned state evidence into persistent storage
        ctx1.storage.record_ui_state(
            game_id="hoi4",
            state_name="research",
            phash="d4e5f60718293a4b",
            confidence=0.92,
            evidence={"slots_detected": 4, "active_researches": 3},
            source="live"
        )

        # Execute start_research skill in dry-run
        exec_res = ctx1.skill_engine.execute_skill("start_research", "hoi4", dry_run=True)
        assert exec_res.success
        assert exec_res.dry_run is True

        # Close session 1 database cleanly
        ctx1.storage.db.close()
        del ctx1

        # Session 2: Reload from persistent database
        ctx2 = UGMContext(db_path=db_path)
        graph2 = ctx2.get_navigation_graph("hoi4")

        # Verify persistent graph routes exist
        path_reload = graph2.plan_route("production", "research")
        assert path_reload.found
        assert len(path_reload.edges) == 2

        # Verify recorded UI state was preserved across restarts
        states = ctx2.storage.get_states(game_id="hoi4")
        assert any(s.state_name == "research" for s in states)
        res_state = next(s for s in states if s.state_name == "research")
        assert res_state.confidence == 0.92
        assert res_state.evidence["slots_detected"] == 4

        # Close session 2 database cleanly
        ctx2.storage.db.close()
