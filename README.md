# Universal Game MCP (UGM) - V2.0

[![Windows CI](https://github.com/QuafdaPoet/universal-game-mcp/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/QuafdaPoet/universal-game-mcp/actions/workflows/windows-ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK v2](https://img.shields.io/badge/MCP%20SDK-v2.3.0-green.svg)](https://github.com/modelcontextprotocol/python-sdk)
[![Version: 2.0.0](https://img.shields.io/badge/Release-v2.0.0-success.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tested on Ryzen 5 5500U](https://img.shields.io/badge/Hardware-AMD%20Ryzen%205%205500U-orange.svg)]()

> **English** | [Tiếng Việt (Vietnamese Guide)](docs/vi/README_vi.md) | [HOI4 Guide](docs/vi/HOI4_GUIDE_vi.md) | [Skill Packs](game_skills/README.md)

**Universal Game MCP (UGM)** is a lightweight, CPU-friendly Model Context Protocol (MCP) server that empowers external AI agents (Claude, Gemini, GPT-4, etc.) to autonomously explore, navigate, learn, and play offline strategy and simulation games without requiring manual scripting, game-specific logic, secondary agent runtimes, or model fine-tuning.

The AI model provides high-level reasoning and decision-making. Universal Game MCP provides:
- **Eyes**: DXGI/GDI screen capture, event-driven OCR (RapidOCR PP-OCRv4), icon detection, and adaptive UI element exploration.
- **Hands**: DirectInput mouse and keyboard control with bounded coordinate safety and dry-run simulation.
- **Memory**: Persistent SQLite knowledge store with WAL mode, perceptual hashing (dHash), point-in-time checkpoints, and rollback.
- **Navigation**: Multi-signal UI state classification and Dijkstra shortest-path menu traversal.
- **Self-Learning & Exploration**: Autonomous interactive UI probing, 4-state knowledge lifecycle (`candidate` → `verified` → `suspect` → `rejected`), and cross-game concept generalization.

---

## ⚡ Measured Benchmark & Performance Profile

Benchmarked on **AMD Ryzen 5 5500U** (6 Cores / 12 Threads, Windows 11 x64, integrated Radeon graphics) under pure CPU execution:

| Subsystem | Measured Latency | Throughput / Notes |
|---|---|---|
| **Synthetic In-Memory Fixture Latency** | **1.79 ms** | In-memory memcpy baseline buffer grab |
| **Visual Diff Verification** | **5.48 ms** | Real-time state verification (`cv2.absdiff` + threshold) |
| **ROI Template Match (OpenCV)** | **43.97 ms** | **13.2x faster** than full screen (Multi-scale + NMS) |
| **Full-Screen Template Match** | **579.94 ms** | Full 1080p scan |
| **Event-driven ROI OCR** | **831.89 ms** | PP-OCRv4 CPU on target bounding box |
| **Full-Screen 1080p OCR** | **2,185.15 ms** | Heavy; avoided at runtime via ROI crop |
| **RAM Footprint (Steady State)** | **136.12 MB** | Entire engine, OCR runtime & SQLite in RAM (< 150 MB) |

> [!NOTE]
> Synthetic in-memory fixture benchmarks are explicitly distinguished from live DXGI desktop duplication captures to prevent misleading performance claims.

---

## 🏗 Architecture (V2 Autonomous Exploration Infrastructure)

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
|    ugm.core      |         |   ugm.memory     |         |  ugm.navigation  |
| - capture (live) |         | - database.py    |         | - recognizer.py  |
| - input (direct) |         |   (SQLite WAL)   |         |   (dHash + OCR)  |
| - ocr (rapidocr) |         | - storage.py     |         | - graph.py       |
| - icon (opencv)  |         | - checkpoints    |         |   (Dijkstra)     |
| - explorer (v2)  |         | - cross_game     |         | - navigator.py   |
| - safety & stop  |         | - dHash & pruner |         +------------------+
+------------------+         +------------------+                  |
         |                            |                            |
         +----------------------------+----------------------------+
                                      |
                                      v
                             +------------------+
                             |    ugm.skills    |
                             | - engine.py      |
                             | - learner.py     |
                             | - loader.py      |
                             +------------------+
```

### Core Subsystems
1. **Autonomous Exploration (`ugm.core.explorer`)**: Generic element discovery, interactive contour and text bounding box grouping, non-destructive probing actions, and layout zone segmentation.
2. **Persistent Knowledge & Lifecycle (`ugm.memory.database`, `ugm.memory.storage`)**: SQLite with WAL mode, point-in-time snapshot checkpoints, versioning, rollback, perceptual hash indexing (dHash), and 4-state knowledge promotion.
3. **Multi-Signal UI State Recognizer (`ugm.navigation.recognizer`)**: Adaptive layout classifier combining perceptual dHash, prominent text anchors, panel geometry, and confidence metrics (`known`, `ambiguous`, `unknown`).
4. **Navigation Graph (`ugm.navigation.graph`, `ugm.navigation.navigator`)**: Dynamic directed graph where nodes are UI states and edges are verified executable actions. Plans lowest-cost routes via Dijkstra with automatic replanning on mismatch.
5. **Verified Self-Learning Engine (`ugm.skills.learner`)**: Procedural learning pipeline: **Observe → Identify → Plan → Act → Verify → Learn → Reuse**. Curated packs remain read-only; learned skills are incrementally validated and promoted.
6. **Skill Execution Engine (`ugm.skills.engine`)**: Sandboxed runtime with allowlisted action primitives (`press_keys`, `click_target`, `locate_and_click`, `click_selector`, `hover`, `wait`, `verify_state`), precondition/postcondition checks, and emergency killswitch.
7. **Cross-Game Concept Store (`ugm.memory.storage`)**: Registers and queries abstract strategy patterns (e.g., economy loops, pause toggles, production queues) shared across multiple games.

---

## 🛠 Complete MCP Tool Reference (34 Tools)

### 1. Perception, Input & Safety Tools (12 Tools)
| Tool Name | Parameters | Description |
|---|---|---|
| `get_game_window` | `window_title`, `process_name` | Finds and attaches to target game window; returns geometry, DPI scale, and client rect. |
| `capture_screen` | `roi`, `save_to_disk`, `include_base64` | Captures current game frame with client rect coordinates. |
| `read_text` | `roi`, `enhance_small_text` | Event-driven RapidOCR reading on normalized ROI. |
| `locate_ui_element` | `selector_id`, `icon_name`, `text`, `threshold`, `roi` | Multimodal target resolution with ambiguity detection and escalation. |
| `locate_icon` | `icon_name`, `threshold`, `roi` | Multi-scale OpenCV template matching with NMS. |
| `hover_target` | `x`, `y`, `duration_ms` | Hovers mouse at normalized `(x, y)` and reads popup tooltip. |
| `click_target` | `x`, `y`, `selector_id`, `dry_run`, `verify` | Clicks target dynamically; verifies visual state before confirming. |
| `press_keys` | `keys`, `duration_ms`, `dry_run` | Sends DirectInput keystrokes (e.g. `['space']` for pause). |
| `verify_state` | `expected_text`, `roi`, `pre_capture_id` | Computes visual difference and validates state transition. |
| `verify_selection` | `expected_text`, `roi`, `pre_capture_id` | Verifies active UI element selection and text status. |
| `emergency_stop` | `reason` | Instant global emergency killswitch halting all input dispatch. |
| `save_training_sample` | `tag`, `label`, `metadata` | Opt-in recording of annotated game frames to `data/samples/`. |

### 2. Memory, Navigation & Skill Execution Tools (11 Tools)
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

### 3. V2 Autonomous Exploration & Knowledge Lifecycle Tools (11 Tools)
| Tool Name | Parameters | Description |
|---|---|---|
| `discover_and_attach_game` | `window_title`, `process_name`, `game_id` | Auto-detects active foreground/named game, registers baseline resolution, and initializes empty knowledge store. |
| `explore_interactive_elements` | `roi`, `min_confidence`, `max_elements` | Scans active screen/ROI for interactive elements (buttons, icons, tabs, inputs) with bounding boxes. |
| `probe_action` | `action`, `x`, `y`, `keys`, `roi`, `duration_ms`, `dry_run` | Non-destructive probing action with pre/post capture diff analysis and isolated change tracking. |
| `manage_knowledge_state` | `item_type`, `item_id`, `new_status`, `reason`, `game_id` | Updates lifecycle state (`candidate`, `verified`, `suspect`, `rejected`) of states, transitions, or skills. |
| `create_knowledge_checkpoint` | `label`, `game_id` | Creates an atomic point-in-time snapshot checkpoint of game knowledge. |
| `rollback_knowledge_checkpoint` | `checkpoint_id`, `game_id` | Restores knowledge state to a previous point-in-time checkpoint. |
| `list_knowledge_checkpoints` | `game_id` | Lists all available checkpoints with creation timestamps and metadata. |
| `query_cross_game_concepts` | `category`, `tag` | Queries reusable abstract concepts and gameplay patterns applicable across titles. |
| `register_cross_game_concept` | `concept_id`, `name`, `category`, `abstract_pattern`, `tags` | Registers an abstract gameplay pattern into cross-game knowledge store. |
| `optimize_skill` | `skill_id`, `game_id`, `strip_redundant`, `tighten_delays` | Optimizes execution latency of a verified skill by removing redundant delays and probes. |
| `prune_temporary_data` | `game_id`, `older_than_days`, `dry_run` | Safely cleans unpromoted candidates, rejected items, and transient exploration data. |

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

# Run automated tests (100% offline, no game required)
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
