# Universal Game MCP (UGM) - V1.5

[![Windows CI](https://github.com/universal-game-mcp/ugm/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/universal-game-mcp/ugm/actions/workflows/windows-ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK v2](https://img.shields.io/badge/MCP%20SDK-v2.3.0-green.svg)](https://github.com/modelcontextprotocol/python-sdk)
[![Version: 1.5.0](https://img.shields.io/badge/Release-v1.5.0-success.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tested on Ryzen 5 5500U](https://img.shields.io/badge/Hardware-AMD%20Ryzen%205%205500U-orange.svg)]()

> **English** | [Tiếng Việt (Vietnamese Guide)](docs/vi/README_vi.md) | [HOI4 Guide](docs/vi/HOI4_GUIDE_vi.md) | [Skill Packs](game_skills/README.md)

**Universal Game MCP (UGM)** is a lightweight, CPU-friendly Model Context Protocol (MCP) server that empowers external AI agents to play, navigate, and learn offline strategy and simulation games (starting with **Hearts of Iron IV**).

The AI model provides reasoning. Universal Game MCP provides:
- **Eyes**: screen capture, event-driven OCR, icon and template matching.
- **Hands**: DirectInput mouse and keyboard control with dry-run protection.
- **Memory**: persistent SQLite knowledge store with WAL mode, schema migrations, and perceptual hashing.
- **Navigation**: UI state recognition and Dijkstra graph traversal across menus and panels.
- **Skills**: validated execution engine for curated and learned macro workflows.
- **Self-Learning**: procedural discovery, validation, promotion, and reuse of action workflows.

---

## ⚡ Measured Benchmark & Performance Profile

Benchmarked on **AMD Ryzen 5 5500U** (6 Cores / 12 Threads, Windows 11 x64, integrated Radeon graphics) under actual CPU execution:

| Subsystem | Measured Latency | Throughput / Notes |
|---|---|---|
| **Synthetic In-Memory Fixture Latency** | **1.79 ms** | In-memory memcpy baseline buffer grab |
| **ROI Template Match (OpenCV)** | **43.97 ms** | **13.2x faster** than full screen (Multi-scale + NMS) |
| **Full-Screen Template Match** | **579.94 ms** | Full 1080p scan |
| **Event-driven ROI OCR** | **831.89 ms** | PP-OCRv4 CPU on target bounding box |
| **Full-Screen 1080p OCR** | **2,185.15 ms** | Heavy; avoided at runtime via ROI crop |
| **Visual Diff Verification** | **5.48 ms** | Real-time state verification (absdiff + threshold) |
| **RAM Footprint (Steady State)** | **136.12 MB** | Entire engine, OCR runtime & SQLite in RAM (< 150 MB) |

> [!NOTE]
> Synthetic in-memory fixture benchmarks are explicitly distinguished from live DXGI desktop duplication captures to prevent misleading performance claims.

---

## 🏗 Architecture (V1.5 Self-Learning Infrastructure)

```
                    +-----------------------------------+
                    |        AI Client / LLM Agent      |
                    +-----------------+-----------------+
                                      | (MCP SDK v2 JSON-RPC)
                                      v
                    +-----------------------------------+
                    |    Universal Game MCP Server      |
                    |          (ugm.server)             |
                    +-----------------+-----------------+
                                      |
         +----------------------------+----------------------------+
         |                            |                            |
         v                            v                            v
+------------------+         +------------------+         +------------------+
|    ugm.core      |         |   ugm.memory     |         |   ugm.navigation |
| - capture (live) |         | - database.py    |         | - recognizer.py  |
| - input (direct) |         |   (SQLite WAL)   |         |   (dHash + OCR)  |
| - ocr (rapidocr) |         | - storage.py     |         | - graph.py       |
| - icon (opencv)  |         | - migrations     |         |   (Dijkstra)     |
| - verifier       |         | - dHash & pruner |         | - navigator.py   |
| - safety         |         +------------------+         +------------------+
+------------------+                  |                            |
         |                            v                            v
         |                   +------------------+         +------------------+
         |                   |    ugm.skills    |         |   ugm.assets     |
         +------------------>| - engine.py      |         | - indexer.py     |
                             | - learner.py     |         |   (.gui/.gfx)    |
                             | - loader.py      |         +------------------+
                             +------------------+
```

### Core V1.5 Subsystems
1. **Persistent Memory (`ugm.memory`)**: SQLite with WAL mode, schema version tracking, perceptual hash matching (dHash), bounded storage pruning, and source attribution (`live` vs `synthetic_fixture`).
2. **UI State Recognizer (`ugm.navigation.recognizer`)**: Multi-signal classifier combining perceptual hashing, event OCR, panel geometry, and confidence scoring across 12 recognized states (`main_menu`, `country_selection`, `main_map`, `politics`, `research`, `production`, `construction`, `national_focus`, `diplomacy`, `recruitment`, `selected_army`, `event_popup`).
3. **Navigation Graph (`ugm.navigation.graph`, `ugm.navigation.navigator`)**: Directed graph where nodes are UI states and edges are verified executable actions. Plans lowest-cost routes via Dijkstra, verifies state after each step, and replans or halts on mismatch.
4. **Prebuilt HOI4 Game Skill Pack (`game_skills/hoi4/`)**: Production-ready reference pack with 15 visual/OCR selectors and 25 verified skills categorized into Tier A (Essential controls), Tier B (Economy & State), and Tier C (Military planning).
5. **HOI4 Strategic Knowledge Base (`game_skills/hoi4/knowledge/`)**: Prebuilt campaign guidelines, rules & bans (FAIR/META), operating loops, economic horizons, and country playbooks (Germany, China, Poland) retrieved on-demand via `hoi4_get_knowledge`.
6. **Advanced Perception (`ugm.adapters.hoi4.perception`)**: Morphological differential tooltip detection, warm parchment / news popup window segmentation, hazard-stripe pause verification, and resilient OCR date parsing.
7. **Verified Self-Learning (`ugm.skills.learner`)**: Procedural learning pipeline: **Observe → Identify → Plan → Act → Verify → Learn → Reuse**. Candidate skills undergo validation track records before promotion to verified skills. Curated skills are strictly protected.
8. **Skill Execution Engine (`ugm.skills.engine`)**: Runtime executor with allowlisted primitives (`press_keys`, `click_target`, `locate_and_click`, `click_selector`, `hover`, `wait`, `verify_state`), precondition checks, postcondition verification, and emergency stop killswitch.
9. **Local Game Asset Indexer (`ugm.assets.indexer`)**: Read-only parser for installed HOI4 `.gui`, `.gfx`, and `.yml` files into local caches without modifying game files or process memory.

---

## 🛠 Complete MCP Tool Reference (26 Tools)

### HOI4 Specific Tools (Prebuilt V1.5)
| Tool Name | Parameters | Description |
|---|---|---|
| `hoi4_get_knowledge` | `topic`, `subtopic` | Retrieves prebuilt campaign playbooks on-demand (`rules_and_modes`, `operating_loop`, `strategy_core`, `country` -> `germany_fair`, `china_fair`, `poland_meta`). |
| `hoi4_detect_environment` | `custom_game_path` | Discovers local HOI4 installations across Steam libraries, checks running process, and verifies OCR readiness. |
| `hoi4_read_topbar` | `detailed` | Event-driven OCR across topbar resources; extracts parsed date, pause status, and speed level when detailed=True. |
| `hoi4_read_tooltip` | `target`, `x`, `y` | Hovers over target selector/coordinate, detects differential tooltip bounding box, and extracts text via OCR. |
| `hoi4_detect_popups` | *(none)* | Scans active view for modal country events or newspaper popups using paper mask segmentation. |

### V1 Perception, Input & Safety Tools
| Tool Name | Parameters | Description |
|---|---|---|
| `get_game_window` | `title_pattern`, `process_name` | Finds and attaches to target game window; returns geometry, DPI scale, and client rect. |
| `capture_screen` | `roi`, `save_to_disk`, `include_base64` | Captures current game frame with client rect coordinates. |
| `read_text` | `roi`, `enhance_small_text` | Event-driven RapidOCR reading on normalized ROI. |
| `locate_ui_element` | `icon_name`, `text`, `selector_id`, `threshold` | Multimodal target resolution with ambiguity detection. |
| `locate_icon` | `icon_name`, `threshold`, `roi` | Multi-scale OpenCV template matching with NMS. |
| `hover_target` | `x`, `y`, `duration_ms` | Hovers mouse at normalized `(x, y)` and reads popup tooltip. |
| `click_target` | `x`, `y`, `selector_id`, `dry_run`, `verify` | Clicks target dynamically; verifies visual state before confirming. |
| `press_keys` | `keys`, `duration_ms`, `dry_run` | Sends DirectInput keystrokes (e.g. `['space']` for pause). |
| `verify_state` | `roi`, `expected_text`, `pre_capture_id` | Computes visual difference and validates state transition. |
| `emergency_stop` | none | Instant global emergency killswitch halting all input. |
| `save_training_sample` | `label`, `metadata` | Opt-in recording of annotated game frames to `data/samples/`. |

### V1.5 Persistent Memory, Navigation & Self-Learning Tools
| Tool Name | Parameters | Description |
|---|---|---|
| `list_game_packs` | none | Lists discovered curated packs and registered games in persistent memory. |
| `get_ui_state` | `game_id`, `hint_state` | Classifies current game UI state (e.g. `main_map`, `production`) with confidence and evidence. |
| `memory_search` | `game_id`, `query`, `search_type`, `limit` | Searches persistent SQLite memory across states, transitions, skills, or action history. |
| `get_navigation_plan` | `target_state`, `start_state`, `game_id` | Calculates optimal multi-step navigation route using Dijkstra algorithm. |
| `navigate_to` | `target_state`, `game_id`, `max_retries`, `dry_run` | Safely traverses menus step-by-step with state verification and replanning. |
| `execute_skill` | `skill_id`, `game_id`, `params`, `dry_run` | Executes a curated or learned Game Skill with precondition/postcondition checks. |
| `learn_skill` | `skill_id`, `name`, `steps`, `game_id`, `preconditions`, `post_verification` | Saves newly discovered action sequence as candidate skill in persistent memory. |
| `list_learned_skills` | `game_id`, `status` | Lists procedurally learned skills, validation track records, and status. |
| `get_learning_status` | `game_id` | Reports persistent memory statistics (states, transitions, action history, skills). |
| `delete_learned_memory` | `game_id`, `skill_id`, `scope` | Safely deletes scoped memory or individual learned skill. |
| `index_game_assets` | `game_path`, `game_id` | Read-only extraction of UI metadata and sprites from installed game files into local cache. |

---

## 🎮 Game Skill Packs (Curated & Learned)

```text
game_skills/
├── README.md                      # Skill pack architecture & JSON schemas
├── _template/                     # Starter template for new games
└── hoi4/                          # Hearts of Iron IV Reference Pack
    ├── manifest.json              # Process names, resolutions, dpi-awareness
    ├── skills/                    # Curated skills (pause_unpause, open_production, increase_speed, select_army_group)
    ├── selectors/                 # Visual and OCR selectors (topbar_factories, political_power, pause_button)
    └── templates/                 # Reference PNG icons for template matching
```

- **Curated Skill Protection**: Curated skills in `game_skills/` are read-only at runtime and can never be overwritten by self-learning calls.
- **Procedural Promotion**: Learned skills start as `candidate` and promote to `verified` after ≥ 3 successful validations with confidence ≥ 0.85.
- **Security Sandboxing**: Execution of arbitrary shell scripts or code is blocked; only allowlisted primitives are permitted.

---

## 🚀 Quickstart

### 1. Installation
Requires **Python 3.12** on Windows 10/11 x64:

```powershell
# Clone repository
git clone https://github.com/QuafdaPoet/universal-game-mcp.git
cd universal-game-mcp

# Create Python 3.12 virtualenv
uv venv --python 3.12 .venv
# or: python -m venv .venv

# Activate virtualenv
.\.venv\Scripts\activate

# Install dependencies in editable mode
pip install -e .
```

### 2. Generate Test Fixtures & Run Offline Tests
```powershell
# Generate offline test fixtures
python scripts/generate_fixtures.py

# Run all 99 automated tests (100% offline, no game required)
pytest -v
```

### 3. Run CPU Latency & RAM Profiling Benchmark
```powershell
python scripts/benchmark_cpu_ram.py
```

### 4. Starting the Universal Game MCP Server
```powershell
# Run with stdio transport (Claude Desktop, Cursor, or Antigravity IDE)
python -m ugm.server

# Or enforce dry-run server-wide (inviolable hardware simulation)
python -m ugm.server --dry-run
```

#### MCP Client Configuration (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "universal-game-mcp": {
      "command": "C:\\Users\\<USER>\\...\\universal-game-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ugm.server"]
    }
  }
}
```

---

## 🔒 Safety and Anti-Cheat Policies

- **Offline & Single-Player Only**: Exclusively intended for single-player, offline strategy/simulation games.
- **Strict Anti-Cheat Blocker**: Active process checks immediately abort and trigger emergency stop if anti-cheat services (EAC, BattlEye, Vanguard, Ricochet, etc.) are detected.
- **Server-Wide Dry-Run**: When launched with `--dry-run`, tool calls cannot override it with `dry_run=False`.
- **Emergency Stop Killswitch**: The `emergency_stop` tool instantly locks out mouse and keyboard inputs.

---

## 📄 License & Attribution

- Source code authored for UGM is licensed under the [MIT License](LICENSE).
- Third-party dependencies are acknowledged in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
