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

The [Windows CI job for PR #27](https://github.com/QuafdaPoet/universal-game-mcp/actions/runs/45934304142/job/140293860935)
measured these separate synthetic workloads. An RSS snapshot is the resident
memory at one instant; a peak working set is the highest measured residency
over the life of the process.

| Workload | Memory measure | Result |
|---|---|---|
| Component benchmark (no MCP screenshot baseline cache) | Final process RSS | 145.25 MiB |
| Cold two-thread RapidOCR, full 900×550 simulator image | Process peak working set after first OCR | 275.64 MB |
| Cold two-thread RapidOCR, padded 153×82 crop of the same image | Process peak working set after first OCR | 292.37 MB |

Neither cold OCR workload meets the **250 MB peak** target. These results do
not measure a live-game capture or gameplay session. See the
[benchmark commands and interpretation](docs/reliability-backlog.md#cold-ocr-phase-profiling)
to reproduce the cold and repeated runs.
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
1. **Autonomous Exploration (`ugm.core.explorer`)**: Heuristic text and shape candidates plus action probes. A detected shape does not establish interactivity, and a live probe can change game state.
2. **Persistent Knowledge & Lifecycle (`ugm.memory.database`, `ugm.memory.storage`)**: SQLite with WAL, knowledge checkpoints, rollback, perceptual hashing (dHash), and four lifecycle statuses. See the reliability backlog for snapshot-integrity and revision gates.
3. **Multi-Signal UI State Recognizer (`ugm.navigation.recognizer`)**: Adaptive layout classifier combining perceptual dHash, prominent text anchors, panel geometry, and confidence metrics (`known`, `ambiguous`, `unknown`).
4. **Navigation Graph (`ugm.navigation.graph`, `ugm.navigation.navigator`)**: Directed graph of UI states and recorded or curated transitions. Dijkstra plans a low-cost route. A proposed route is not proof that every edge is verified for the current game session.
5. **Skill Learning (`ugm.skills.learner`)**: Accepts skill definitions from the external agent, tracks outcomes, and separates learned records from curated packs. End-to-end zero-knowledge learning is not yet validated in a real game.
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
- **Procedural Promotion**: Learned skills have lifecycle statuses. Until the evidence and revision gates in the backlog pass, those statuses are not proof of a reproducible gameplay rule.
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

**Dataset collection is off by default.** To allow `save_training_sample` to write attached game-client images under `data/samples`, set `UGM_DATASET_COLLECTION=1` in the MCP server process environment and restart it. A tool caller cannot opt in for the operator; `force=True` is only a one-sample shortcut on an explicitly configured fixture. Dataset sidecars separately label fixture, dry-run observations, and live observations. They are not verified action or skill evidence.

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

The benchmark is synthetic, not a live-game performance promise. For a no-input Windows capture/OCR procedure, current limitations, and the test matrix, see the [reliability backlog](docs/reliability-backlog.md). Keep generated fixture changes and personal database files out of commits unless intentionally updating fixtures.

---

## 🔒 Safety and Anti-Cheat Policies

- **Offline & Single-Player Only**: Exclusively intended for single-player, offline strategy/simulation games.
- **Anti-Cheat Blocker**: Input checks block recognized anti-cheat processes. This is a safeguard, not detection coverage for every online game or protection against account penalties.
- **Server-Wide Dry-Run**: When launched with `--dry-run`, tool calls cannot override it with `dry_run=False`.
- **Knowledge isolation**: Fixture and server-wide dry-run sessions use a separate SQLite file (`<db-path>.simulation`); live sessions cannot open it. A per-call dry-run does not update live observations, action history, skill validation, or navigation reliability.
- **Evidence levels**: `simulated` means no live action evidence; `observed` means a signal such as OCR or a visual difference was detected; `verified` means an explicit live check matched. A dry-run skill or navigation result may indicate acceptance (`success`), but does not indicate input execution or postcondition verification. A probe's visual difference alone does not establish a gameplay rule.
- **Live probe budget**: `probe_action` accepts `timeout_s` (0.05–10 seconds, default 5) and requires explicit normalized coordinates for click/hover. It waits for a stable visual change or samples through the deadline without accepting transient flashes. OCR compares the baseline and stable post-action frame only when enough budget remains. Check `executed`, `timed_out`, `timeout_phase`, and `visual_diff_measured` separately: an input sent before a timeout may still have changed game state. The deadline is cooperative; an in-flight native call may overrun, and a probe never sends a generic undo.
- **Emergency Stop**: The `emergency_stop` tool blocks subsequent UGM hardware actions. It does not undo an action already delivered to the game.

---

## 📄 License & Attribution

- Source code authored for UGM is licensed under the [MIT License](LICENSE).
- Third-party dependencies are acknowledged in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
