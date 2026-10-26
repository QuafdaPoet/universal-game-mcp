# Universal Game MCP (UGM)

[![Windows CI](https://github.com/universal-game-mcp/ugm/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/universal-game-mcp/ugm/actions/workflows/windows-ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK v2](https://img.shields.io/badge/MCP%20SDK-v2.3.0-green.svg)](https://github.com/modelcontextprotocol/python-sdk)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tested on Ryzen 5 5500U](https://img.shields.io/badge/Hardware-AMD%20Ryzen%205%205500U-orange.svg)]()

> **English** | [Tiếng Việt (Vietnamese Guide)](docs/vi/README_vi.md)

**Universal Game MCP (UGM)** is a lightweight, CPU-friendly open-source Model Context Protocol (MCP) server that empowers AI assistants to observe, read text, locate icons, send DirectInput keyboard/mouse controls, and verify action outcomes in offline games (starting with **Hearts of Iron IV**).

---

## ⚡ Measured Benchmark & Performance Profile

Benchmarked on **AMD Ryzen 5 5500U** (6 Cores / 12 Threads, Windows 11 x64) under actual CPU execution:

| Subsystem | Measured Latency | Throughput / Speedup | Notes |
|---|---|---|---|
| **Capture (DMA/Memory Buffer)** | **2.20 ms** | ~450 FPS buffer pull | DXGI Desktop Duplication API / DMA |
| **ROI Template Match (OpenCV)** | **51.27 ms** | **14.5x faster** than full screen | Multi-scale template matching + NMS |
| **Full-Screen Template Match** | **743.10 ms** | Baseline | Full 1080p scan |
| **Event-driven ROI OCR** | **705.05 ms** | **3.0x faster** than full screen | PP-OCRv4 CPU (no GPU required) |
| **Full-Screen 1080p OCR** | **2,115.99 ms** | Heavy (unoptimized) | Avoided at runtime via ROI crop |
| **Visual Diff Verification** | **4.58 ms** | Real-time state check | Absdiff + threshold check |
| **RAM Footprint (Steady State)** | **117.65 MB** | **< 120 MB RAM total** | Extremely light on CPU & memory |

---

## 🏗 Architecture & Design Principles

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
        +---------------------------+---------------------------+
        |                           |                           |
        v                           v                           v
+---------------+           +---------------+           +---------------+
|  ugm.core     |           | ugm.adapters  |           | ugm.safety    |
| - capture     |           | - hoi4        |           | - killswitch  |
| - ocr         |           | - offline_sim |           | - dry_run     |
| - icon        |           | - generic     |           | - bounds      |
| - input       |           +---------------+           | - anti-cheat  |
| - verifier    |                                       +---------------+
+---------------+
```

1. **Game-Agnostic Core**: The core subsystems (`capture`, `ocr`, `icon`, `input`, `verifier`, `safety`) operate entirely independently of any specific title.
2. **Modular Adapters (Plugins)**:
   - `ugm.adapters.hoi4`: Hearts of Iron IV plugin (Topbar metrics, research, diplomacy, pause key).
   - `ugm.adapters.sim`: Self-contained offline strategy simulator for testing without commercial games installed.
   - `ugm.adapters.generic`: Generic adapter for arbitrary strategy/simulation titles.
3. **Event-Driven ROI OCR**: Rather than burning CPU cycles doing continuous 60fps full-screen OCR, UGM captures and OCRs *only on demand* when triggered by specific events (reading a tooltip, reading topbar statistics, reading dialogue options).
4. **Coordinate Normalization**: All returned bounding boxes and points are normalized `[0.0, 1.0]` relative to the target game window client rectangle.
5. **Safety by Default**:
   - Ambiguity protection: If icon/text detection is ambiguous, UGM reports ambiguity so AI can hover to inspect tooltip rather than clicking blindly.
   - Out-of-bounds containment: Input commands outside target window are rejected.
   - Dry-Run mode: Allows simulating clicks without dispatching hardware input.
   - Emergency Stop: Instant global killswitch blocks all further inputs.
   - Anti-Cheat Blocker: Strictly refuses execution if online anti-cheat engines (EAC, BattlEye, Vanguard) are detected.
6. **Privacy First**: Dataset recording is opt-in (`disabled by default`) and automatically excluded from Git.

---

## 🛠 Standard MCP Tools

| Tool Name | Prioritized V1 Capabilities | Description |
|---|---|---|
| `get_game_window` | Windows geometry & DPI scaling | Finds and attaches to target game window; returns client rectangle, DPI scale, and focus state. |
| `capture_screen` | Native resolution capture | Captures current game frame (supports ROI cropping, Base64 preview, and disk saving). |
| `read_text` | Small UI text & ROI OCR | Event-driven RapidOCR reading on normalized ROI with small-text enhancement. |
| `locate_ui_element` | Multimodal target resolution | Locates UI elements via icon template, OCR text, or registered selector with ambiguity detection. |
| `locate_icon` | Backward-compatible alias | Multi-scale OpenCV template matching with NMS and ambiguity detection. |
| `hover_target` | Tooltip verification | Hovers mouse at normalized `(x, y)` and reads popup tooltip via event OCR. |
| `click_target` | State verification & dynamic targets | Clicks at coordinates `(x, y)` or dynamically resolved selectors; verifies visual state before confirming success. |
| `press_keys` | DirectInput keystrokes | Sends DirectInput keystrokes (e.g. `['space']` for pause, `['num+']` for speed) with timeout. |
| `verify_state` | Visual diff & text check | Computes visual state difference and optional expected text verification. |
| `verify_selection` | Backward-compatible alias | Legacy alias for visual state change verification. |
| `emergency_stop` | Safety killswitch | Instant emergency killswitch halting all inputs and rejecting subsequent actions. |
| `save_training_sample` | Privacy-first dataset | Opt-in recording of annotated game frames to `data/samples/`. |

---

## 🎮 Game Skills Architecture (Modular & Independent)

The `game_skills/` directory provides game-independent declarative manifests, UI selectors, templates, and macro skills:

```text
game_skills/
├── README.md                      # Architecture and specifications
├── _template/                     # Starter template for new games
│   ├── manifest.json              # Versioned manifest schema (1.0.0)
│   ├── skills/                    # Atomic skills (e.g. example_skill.json)
│   ├── selectors/                 # Visual and OCR selectors (e.g. example_selector.json)
│   └── templates/                 # CV image templates
└── hoi4/                          # Hearts of Iron IV reference pack
    ├── manifest.json              # HOI4 metadata, process names, resolutions
    ├── README.md                  # HOI4 reference pack documentation
    ├── skills/                    # pause_unpause, open_production, select_army_group, increase_speed
    ├── selectors/                 # topbar_factories, political_power, pause_button, army_group_leader
    ├── templates/                 # PNG templates for template matching
    └── tests/                     # Manifest validation fixtures
```

- **Reference Pack**: HOI4 is the first reference game pack, not a dependency of MCP Core.
- **Dynamic Selectors**: Avoids hardcoding screen pixel coordinates; resolves elements dynamically.
- **Strict Verification**: Never reports success based solely on whether a mouse click was sent; compares pre/post frames.
- **Ambiguity Rejection**: Rejects ambiguous targets and outputs structured `VisionAgentEscalation` payloads when uncertain.
- **V1.5 Contract Preparation**: Defines lightweight interface specifications for Registry, Knowledge Storage, and Navigation Graph without early implementation.

---

## 🚀 Quickstart

### 1. Installation

Requires **Python 3.12** on Windows 10/11 x64:

```powershell
# Clone or enter directory
cd universal-game-mcp

# Create Python 3.12 virtualenv
uv venv --python 3.12 .venv
# or: python -m venv .venv

# Activate virtualenv
.\.venv\Scripts\activate

# Install pinned dependencies
pip install -r requirements.txt
```

### 2. Generate Test Fixtures & Icons

```powershell
python scripts/generate_fixtures.py
```

### 3. Run Automated Tests (No Game Required)

```powershell
pytest -v
```
*All 58 unit and integration tests run entirely offline using image fixtures.*

### 4. Run the CPU Latency & RAM Benchmark

```powershell
python scripts/benchmark_cpu_ram.py
```

### 5. Run the End-to-End Offline Demo

If you do not have Hearts of Iron IV installed, run the self-contained offline simulator demo:

```powershell
python scripts/demo_offline_sim.py
```

Or launch the interactive Tkinter game simulator:

```powershell
python scripts/offline_game_sim.py
```

### 6. Starting the MCP Server

```powershell
# Run with stdio transport (for Claude Desktop, Cursor, or Antigravity MCP)
python -m ugm.server
```

Configuration snippet for Claude Desktop or MCP client config (`mcpServers`):

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

## 🔒 Safety and Compliance

- **Offline & Single Player Only**: UGM is exclusively created for offline, single-player games and educational testing.
- **Zero Anti-Cheat Bypass**: UGM includes a built-in process blacklist checking for anti-cheat services. Any detection triggers an immediate emergency shutdown.
- **Dry-run First**: AI agents are encouraged to run actions with `dry_run=True` before issuing live inputs.

---

## 📄 License

- Source code authored for UGM is licensed under the [MIT License](LICENSE).
- Third-party dependencies are acknowledged in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
