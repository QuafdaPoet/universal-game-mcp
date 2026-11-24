# Universal Game MCP (UGM) · V2 development

[![Windows CI](https://github.com/QuafdaPoet/universal-game-mcp/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/QuafdaPoet/universal-game-mcp/actions/workflows/windows-ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK v2](https://img.shields.io/badge/MCP%20SDK-v2.3.0-green.svg)](https://github.com/modelcontextprotocol/python-sdk)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tested on Ryzen 5 5500U](https://img.shields.io/badge/Hardware-AMD%20Ryzen%205%205500U-orange.svg)]()

> **English** | [Tiếng Việt (Vietnamese Guide)](docs/vi/README_vi.md) | [HOI4 Guide](docs/vi/HOI4_GUIDE_vi.md) | [Skill Packs](game_skills/README.md)

**Universal Game MCP (UGM)** is a lightweight, CPU-friendly Model Context Protocol (MCP) server that empowers external AI agents (Claude, Gemini, GPT-4, etc.) to inspect and interact with local, offline strategy and simulation games. Hearts of Iron IV is the bundled reference pack; other games may require their own pack and live validation.

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

## MCP tools

The stdio server implements MCP tool discovery. Use the client's `tools/list` result for the current argument names, types, and defaults. Start with these calls on the synthetic fixture:

| Tool | Arguments for first use |
|---|---|
| `list_game_packs` | `{}` |
| `capture_screen` | `{}` captures the current fixture without saving an image |
| `read_text` | `{"roi": [0, 0, 1, 0.1]}` reads the top strip |
| `emergency_stop` | `{"reason": "stop requested"}` stops input |

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

## Quickstart

Python 3.12 is the CI baseline. Live window discovery and input require Windows 10/11 x64. The synthetic fixture can be used without an installed game.

```powershell
git clone https://github.com/QuafdaPoet/universal-game-mcp.git
cd universal-game-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m ugm.server --fixture tests/fixtures/sim_fixture.png --dry-run
```

The last command starts a **stdio** server that expects a client; output is not an interactive shell. In fixture mode a missing or unreadable image stops startup, and hardware input cannot be enabled by a tool call. A minimal MCP client configuration:

```json
{
  "mcpServers": {
    "universal-game-mcp": {
      "command": "C:/path/to/universal-game-mcp/.venv/Scripts/python.exe",
      "args": [
        "-m", "ugm.server", "--dry-run",
        "--fixture", "C:/path/to/universal-game-mcp/tests/fixtures/sim_fixture.png",
        "--db-path", "C:/path/to/universal-game-mcp/data/memory/demo.db"
      ]
    }
  }
}
```

Use absolute paths in client configuration. Built-in HOI4 resources load from the installed package regardless of working directory. The default database remains `data/memory/ugm_memory.db` relative to launch directory, for compatibility; use `--db-path` to select it explicitly. Dry-run prevents hardware input; it is not a filesystem sandbox, and local data tools can still write files or SQLite records. Use a trusted local MCP client and a separate database for trials.

### Developer verification

After activating the environment, run the checks from the checkout root:

```text
python scripts/generate_fixtures.py
python -m pytest -v --tb=short
python scripts/smoke_test_mcp_subprocess.py
python scripts/benchmark_cpu_ram.py
python scripts/demo_offline_sim.py
python -m pip wheel --no-deps -w dist .
```

The benchmark is synthetic, not a live-game performance promise. Keep generated fixture changes and personal database files out of commits unless intentionally updating fixtures.

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
