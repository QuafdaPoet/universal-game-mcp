# Hearts of Iron IV Prebuilt Integration Report

**Universal Game MCP (UGM) Version:** 1.5.0  
**Target Game:** Hearts of Iron IV (Clausewitz Engine)  
**Discovered Local Installation:** `F:\SteamLibrary\steamapps\common\Hearts of Iron IV`  
**Discovered Game Version:** `Operation Postern v1.19.3.0.c01a`  
**Branch:** `feat/hoi4-prebuilt-integration`  

---

## 1. Architectural Overview

Universal Game MCP (UGM) V1.5 provides the core framework for multi-modal game observation, optical character recognition, navigation graph planning, verifiable skill execution, and persistent memory.

Hearts of Iron IV (HOI4) is integrated via a clean **layered adapter and skill pack pattern**:
- **Zero Process Injection:** Safe, non-invasive execution using desktop window discovery, DirectX/BetterCam capture, event-driven RapidOCR, and DirectInput keyboard/mouse dispatch.
- **Decoupled Architecture:** High-level strategic doctrine is strictly segregated from low-level UI selectors and navigation edges.
- **On-Demand Knowledge Delivery:** Comprehensive campaign guidelines and country playbooks load selectively via MCP tools to preserve LLM context budget.
- **Offline / Simulation-First:** Fully operable in offline fixture mode for CI testing, development, and air-gapped gameplay without game modification.

---

## 2. Integrated Subsystems & Deliverables

### A. Prebuilt Strategic Knowledge
Located in `game_skills/hoi4/knowledge/` and exposed via MCP tool `hoi4_get_knowledge(topic, subtopic)`:
1. `meta.json`: Attribution manifest to upstream commit `6b91a2a43edfeab60a415ec2ddd9b71d215b8a56`, version compatibility, and license declarations.
2. `rules_and_modes.json`: Standardized FAIR (house rules against space marines and exploit loops) and META guidelines.
3. `operating_loop.json`: Cadence schemas (peacetime vs wartime), standing plan priority order, and pause policies.
4. `strategy_core.json`: Economy payback horizons (2.5–3 years), research focus heuristics, stockpile coverage limits, and emergency production reassignments.
5. `countries/germany_fair.json`: Phased progression from Rhineland remilitarization through Danzig, Fall Gelb, Sea Lion, and Barbarossa.
6. `countries/china_fair.json`: Defense-in-depth along river barriers, inland industrial relocation, warlord integration, and coastal port garrisons.
7. `countries/poland_meta.json`: Monarchist Baltic alliance, Romanian bridgehead fort anchors, and mobile encirclement tactics.

### B. UI State Catalog & Navigation Graph
The HOI4 state machine is expanded to **12 recognized observable states** in `ugm/navigation/recognizer.py`:
- `main_menu`: Top-level game menu screen.
- `country_selection`: Scenario / country picker (1936 / 1939).
- `main_map`: World map view with active topbar and clock.
- `politics`: Government laws, ministers, and focus slot drawer.
- `national_focus`: Full-screen national focus tree branch view.
- `production`: Military factory allocations and naval shipyard queues.
- `construction`: Civilian factory infrastructure project queue.
- `research`: 4–5 technology slots and technology trees.
- `diplomacy`: Global world tension, relations, and faction alignments.
- `recruitment`: Division template designer and training queues.
- `selected_army`: Bottom military theatre bar with commander card active.
- `event_popup`: Modal country event (parchment) or news popup (grey paper).

The navigation graph in `ugm/server.py` registers bidirectional transitions between `main_map` and all panels via hotkeys (`y`, `t`, `w`, `e`, `u`, `shift+q`, `escape`, `enter`).

### C. Prebuilt Game Skill Pack (`game_skills/hoi4/`)
Loaded dynamically and verified against UGM contracts:
- **15 Selectors:** `topbar_date`, `topbar_speed`, `topbar_stability`, `topbar_manpower`, `topbar_factories`, `political_power`, `pause_button`, `national_focus_slot`, `research_slot_1`, `production_line_1`, `construction_queue`, `event_popup_option`, `army_card`, `army_group_leader`, `recruit_tab`.
- **25 Skills Across 3 Tiers:**
  - *Tier A (Essential):* `pause_unpause`, `set_speed`, `decrease_speed`, `increase_speed`, `close_dialog`, `open_production`, `open_research`, `open_construction`, `open_politics`, `open_national_focus`, `read_topbar`, `read_tooltip`.
  - *Tier B (Economy & State):* `inspect_research_slots`, `start_research`, `inspect_national_focus`, `select_national_focus`, `inspect_production_lines`, `adjust_factory_allocation`, `inspect_construction_queue`, `queue_construction`.
  - *Tier C (Military):* `list_visible_armies`, `select_army`, `select_army_group`, `assign_commander`, `inspect_army_orders`.

### D. Advanced Perception Module (`ugm/adapters/hoi4/perception.py`)
- **Differential Tooltip Detection:** Computes before/after frame deltas upon hover, applies morphological closing, and extracts localized tooltip text.
- **Paper Mask Popup Segmentation:** Resolves warm parchment and grey news paper masks to detect modal dialog boundaries and option buttons.
- **Resilient Date Parser:** Extracts ISO and HOI4 dates while substituting common OCR digit confusions (`l` -> `1`, `O` -> `0`, `S` -> `5`).
- **Hazard-Stripe Pause & Speed Analysis:** Detects red diagonal striping on paused date boxes and counts active speed segments.

### E. Game Environment & Asset Indexing
- **Auto-Discovery:** Detects HOI4 across Steam library configurations, verifies executable status, process presence, and OCR engines (`RapidOCR`, `WinOCR`).
- **Safe Asset Indexer:** Auto-scanned 22,653 sprite definitions, 3,157 UI window layouts, and 164,568 localization keys into `data/asset_index/hoi4_asset_index.json` without modifying game files.
