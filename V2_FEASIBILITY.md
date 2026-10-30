# Universal Game MCP (UGM) V2 - Feasibility Audit & Architectural Blueprint

**Lead Architect & Implementation Engineer:** Universal Game MCP Core Team  
**Date:** October 30, 2026  
**Status:** **APPROVED FOR IMPLEMENTATION (FEASIBLE)**  
**Branch:** `feat/ugm-v2`  
**Reference Codebase:** Universal Game MCP v1.5.0 (`131f25c`)

---

## 1. Executive Summary & The V2 Vision

The Universal Game MCP V2 mission is straightforward:
> **ONE externally connected AI Agent** uses standard generic MCP tools to observe, explore, learn, and play previously unsupported offline games **without any manually authored per-game GameSkill**, hardcoded gameplay logic, additional secondary AI agent runtime, local model fine-tuning, or heavyweight framework.

In V2:
- **GameSkill starts completely empty** for every newly encountered game.
- The connected external AI (such as Claude, Gemini, or GPT-4) uses generic vision, input, and memory MCP tools to autonomously discover controls, UI affordances, menu structures, game rules, and high-level strategies.
- The connected AI verifies each empirical observation, saves structured candidate knowledge to local persistent SQLite memory, resumes learning seamlessly across sessions, learns from unexpected failures, and gradually optimizes gameplay speed and reliability.
- Target: An initial **5 to 30 minute exploration phase** to establish basic autonomous gameplay loop in low-reflex offline games (Paradox grand strategy like HOI4/EU4, turn-based 4X like Civ VI, colony/factory sims like RimWorld/Factorio). Fast-twitch FPS and competitive multiplayer remain strictly out of scope.
- **CPU-First & Lightweight**: Preserves V1.5's CPU-only design (<250 MB steady-state RAM, <100 ms ROI OpenCV matching, <1.5 s RapidOCR inference).

---

## 2. Reusable V1.5 Components vs V2 Requirements Mapping

| V1.5 Subsystem / Component | V1.5 Capability | V2 Requirement | Gap / Architectural Delta | Reusability |
|---|---|---|---|---|
| **Screen Capture** (`ugm/core/capture.py`) | BetterCam, MSS, GDI BitBlt, FixtureBackend | Real-time 1080p capture, ROI cropping, base64 encoding | Temporary capture cleanup, bounded memory cache | **100% Reusable** |
| **Window Management** (`ugm/core/window.py`) | Win32 window lookup, DPI scaling, client rect isolation | Auto-discovery of active game windows, foreground detection | Add foreground window discovery & process inspection | **95% Reusable** |
| **OCR Engine** (`ugm/core/ocr.py`) | RapidOCR PP-OCRv4 CPU inference, ROI cropping | Event-driven text reading for buttons, labels, stats | Adaptive layout OCR without hardcoded ROI assumptions | **100% Reusable** |
| **Icon Detection** (`ugm/core/icon.py`) | OpenCV multi-scale template matching + NMS | Visual template discovery, dynamic anchor identification | Register dynamic selectors directly to memory | **90% Reusable** |
| **Input Controller** (`ugm/core/input.py`) | Coordinate mapping, bounds check, rate limiting, dry-run | Safe keyboard/mouse dispatch within client area | Non-destructive probing mode, bounded action safety | **95% Reusable** |
| **Action Verifier** (`ugm/core/verifier.py`) | Visual diff (`cv2.absdiff`), pixel diff ratio, hover tooltip | Verify state changes after probing or actions | Differentiate execution errors from falsified rules | **90% Reusable** |
| **Safety & Anti-Cheat** (`ugm/core/safety.py`) | Emergency stop killswitch, anti-cheat blacklist, dry-run | Inviolable bounds containment and instant killswitch | Bounded exploration limits (max steps per sequence) | **95% Reusable** |
| **Target Selection** (`ugm/core/target.py`) | TargetResolver, TargetExecutor, ambiguity escalation | Dynamic element resolution with fallback escalation | Generic UI element extraction for exploration | **90% Reusable** |
| **SQLite Memory** (`ugm/memory/database.py`) | SQLite WAL mode, Schema v1, dHash perceptual hashing | Point-in-time checkpoints, versioning, rollback | Schema v2 migration (`checkpoints`, `cross_game_concepts`, `status`) | **85% Reusable** |
| **Knowledge Storage** (`ugm/memory/storage.py`) | UI states, transitions, learned skills, action history | 4 knowledge states (`candidate`/`verified`/`suspect`/`rejected`), rollback | State transition logic, snapshot creation, pruning | **80% Reusable** |
| **Navigation Graph** (`ugm/navigation/graph.py`) | Dijkstra shortest path, dynamic cost adaptation on failure | Multi-step menu pathfinding from discovered transitions | Empty graph initialization, auto-edge ingestion | **95% Reusable** |
| **UI Recognizer** (`ugm/navigation/recognizer.py`) | Perceptual hash + hardcoded HOI4/Sim heuristics | Adaptive UI layout recognition for any unknown game | Remove hardcoded dependencies; generic layout anchors | **75% Reusable** |
| **Skill Engine** (`ugm/skills/engine.py`) | Allowlisted action primitives, parameter substitution | Step execution, latency tracking, version tracking | Execution metrics recording for gradual optimization | **90% Reusable** |
| **Skill Learner** (`ugm/skills/learner.py`) | Candidate recording, validation counting, auto-promotion | 4-state lifecycle, rollback, resilience against transient failure | Distinguish transient execution failure vs rule failure | **80% Reusable** |
| **MCP Server** (`ugm/server.py`) | 21 MCP tools exposed via official MCP SDK | AI-accessible exploration, probing, versioning tools | Expose new generic V2 tools, 100% backward compatible | **85% Reusable** |

---

## 3. Exact Missing Capabilities Identified

### 3.1. Automatic Game Discovery & Empty Knowledge Bootstrap
- **V1.5 Limitation:** `UGMContext` hardcoded initial metadata for `"hoi4"` and `"offline_sim"`. Connecting to a previously unseen game required manual code or pre-existing JSON manifests.
- **V2 Solution:** `discover_and_attach_game` MCP tool. Inspects the foreground or specified window, sanitizes the game identifier, initializes empty SQLite records, registers the baseline resolution and DPI scale, and provisions an empty `NavigationGraph` ready for exploration.

### 3.2. AI-Accessible Exploration & Adaptive UI Observation
- **V1.5 Limitation:** `UIStateRecognizer` relied on hardcoded HOI4 heuristics (`_evaluate_hoi4_signals`). Generic mode only scanned raw OCR text without structural analysis.
- **V2 Solution:** 
  1. `explore_interactive_elements` tool: Scans the active screen or ROI, detects interactive candidate regions (text buttons, icons, status bars, tab panels), filters noise, and returns structured targets with normalized bounding boxes and suggested probe actions.
  2. `observe_ui_adaptive` tool: Computes perceptual dHash, segments layout into functional zones (header bar, side navigation, main viewport, bottom panel), extracts salient textual anchors, and matches against stored state fingerprints.

### 3.3. Observe-Act-Verify-Learn Probing Pipeline
- **V1.5 Limitation:** Probing required multiple separate tool calls (`capture_screen` $\to$ `click_target` $\to$ `verify_state` $\to$ `learn_skill`), with no built-in transaction rollback or diff change isolation.
- **V2 Solution:** `probe_action` MCP tool. Atomically captures baseline pre-frame, applies a candidate action primitive under strict bounds and dry-run safety, waits for UI stabilization, captures post-frame, computes visual and textual diff, extracts tooltips or newly opened sub-panels, and returns empirical verification evidence with an optional auto-revert (e.g. sending Esc if a probe opened an unwanted modal).

### 3.4. 4-State Knowledge Lifecycle & Resilience Against Isolated Failures
- **V1.5 Limitation:** Skills only had `candidate`, `verified`, and `rejected`. A single failure dropped confidence immediately. Transitions and selectors lacked state machines.
- **V2 Solution:**
  - Formal 4-state lifecycle: `candidate` $\to$ `verified` $\leftrightarrow$ `suspect` $\to$ `rejected`.
  - **Invariant:** *One isolated failure must never overwrite previously verified knowledge.*
  - **Execution Error vs. Rule Failure Distinction:**
    - If execution fails due to window focus loss, coordinate clipping, or transient OS delay $\to$ flagged as `execution_error`; knowledge record remains intact.
    - If input dispatches successfully but postcondition visual verification fails $\to$ recorded as `postcondition_failure`. A `verified` skill or transition requires **$\ge 2$ consecutive postcondition failures** to transition to `suspect`, and never transitions directly to `rejected` without repeated failures or explicit AI rejection.
  - `manage_knowledge_state` MCP tool for AI governance of knowledge states.

### 3.5. Knowledge Versioning, Checkpointing & Rollback
- **V1.5 Limitation:** No snapshot or rollback capability. False inferences or corrupted learning could not be reverted.
- **V2 Solution:**
  - `checkpoints` table in SQLite schema v2.
  - `create_knowledge_checkpoint`: Saves a point-in-time snapshot of all states, transitions, selectors, and skills for a game with a descriptive label.
  - `rollback_knowledge_checkpoint`: Atomically restores the game's knowledge base to any chosen checkpoint.
  - `list_knowledge_checkpoints`: Lists available snapshots with timestamps and metrics.

### 3.6. Gradual Skill Optimization
- **V1.5 Limitation:** Skills were static once learned; no mechanism existed to optimize delays, prune redundant steps, or bump version numbers.
- **V2 Solution:** `optimize_skill` MCP tool. Accepts optimized step sequences (e.g. reduced wait times, direct hotkey shortcuts replacing multi-click menus), benchmarks latency, bumps skill version (e.g. 1.0.0 $\to$ 1.1.0), and maintains historical telemetry.

### 3.7. Reusable Cross-Game Concepts
- **V1.5 Limitation:** HOI4-specific keybindings (space for pause, y for production) were hardcoded.
- **V2 Solution:** Universal concepts library stored in persistent memory (`cross_game_concepts`). Pre-populates generic heuristics for turn-based and grand-strategy games (e.g. `pause_toggle` $\to$ `['space']`, `cancel_dialog` $\to$ `['escape']`, `speed_up` $\to$ `['num+']` or `['3']`). External AI can query these prior heuristics via `query_cross_game_concepts` during the initial exploration phase of an unknown game.

### 3.8. Bounded Exploration & Automatic Cleanup
- **V1.5 Limitation:** Temporary capture cache had a loose 10-item limit; no disk cleanup for old saved captures or bloated SQLite action history.
- **V2 Solution:**
  - Bounded exploration safeguards: maximum consecutive probe depth limit, mandatory client rect containment, automatic return-to-safe-state escape sequence.
  - Automatic cleanup: `prune_temporary_data` MCP tool to purge stale captures and prune action logs exceeding retention ceilings (e.g. >1,000 records).

---

## 4. Technical Limitations & Dependency Risks

1. **Host Environment Differences (Termux / Linux vs Windows 11):**
   - Universal Game MCP production target is **Windows 10/11 x64** where native GDI, DirectX Desktop Duplication (`bettercam`), and DirectInput (`pydirectinput`) operate against real game windows.
   - On Android Termux / Linux headless environments, binary packages like `bettercam` and Windows Win32 APIs do not exist. Offline testing relies on mock shims and synthetic in-memory fixtures.
   - *Risk Mitigation:* Centralized shims in `tests/conftest.py` ensure all core algorithms (graph search, SQLite persistence, target resolution, state machines, checkpoint rollback) are rigorously tested and verified offline. Genuine test results must distinguish mock platform execution from live Windows validation.

2. **CPU and Latency Budgets:**
   - Full-screen OCR on 1080p frames takes ~2.1 seconds on CPU. Doing full-screen OCR every probe would violate the 5-30 minute exploration budget.
   - *Risk Mitigation:* Event-driven ROI cropping and layout zoning restrict OCR to small sub-regions (topbar, dialog headers), keeping inference latency <150 ms per probe. Perceptual dHash (<2 ms) handles screen similarity comparisons.

3. **External AI Reasoning Independence:**
   - UGM MCP provides tools and memory; it must **never embed an autonomous LLM loop or local neural network**. The external connected AI controls the exploration loop.
   - *Risk Mitigation:* Keep all MCP tools synchronous, deterministic, and idempotent where possible, returning rich structured evidence so the external AI can make informed decisions.

---

## 5. Minimal Architecture Changes & Implementation Options

### Option 1: Monolithic Overhaul (REJECTED)
Replace existing SQLite memory and navigation subsystems with an external graph database and embedded RL framework.  
*Why Rejected:* Violates the CPU-first, lightweight, zero-additional-runtime constraint. Introduces heavyweight dependencies and breaks backward compatibility.

### Option 2: Clean Incremental Modular Extension (RECOMMENDED & SELECTED)
Build upon the solid V1.5 foundation by adding:
1. **Schema Migration v2 (`ugm/memory/database.py`):**
   - Add `checkpoints` table.
   - Add `cross_game_concepts` table.
   - Add `status` and `consecutive_failures` columns to `navigation_transitions` and `selectors_memory`.
   - Add `history_version` to `learned_skills`.
2. **Knowledge Lifecycle & Checkpoint Engine (`ugm/memory/storage.py`):**
   - Checkpoint creation, rollback, and listing.
   - 4-state lifecycle management with failure categorization (execution error vs rule postcondition failure).
   - Cross-game concepts storage and lookup.
   - Temporary data pruning.
3. **Generic UI Explorer & Probing Core (`ugm/core/explorer.py`):**
   - Candidate interactive element detection via OpenCV contours, text bounding boxes, and layout heuristics.
   - Observe-Act-Verify probe execution with automatic stabilization and diff reporting.
4. **Adaptive UI State Recognizer Extension (`ugm/navigation/recognizer.py`):**
   - Decouple from game-specific branches; use dynamic visual anchors and layout zoning for unknown games.
5. **New V2 MCP Tools (`ugm/server.py`):**
   - `discover_and_attach_game`
   - `explore_interactive_elements`
   - `probe_action`
   - `manage_knowledge_state`
   - `create_knowledge_checkpoint`
   - `rollback_knowledge_checkpoint`
   - `list_knowledge_checkpoints`
   - `query_cross_game_concepts`
   - `optimize_skill`
   - `prune_temporary_data`
   - Retain 100% of V1.0 / V1.5 tools and contracts for full backward compatibility.

---

## 6. Measurable Acceptance Criteria (Phase B Test Plan)

A successful V2 implementation must pass the following verifiable acceptance criteria via automated tests:

1. **AC-1 (Zero-Code Game Discovery):** An unknown game window is discovered and attached; SQLite registers it with 0 states, 0 selectors, 0 transitions, and 0 skills.
2. **AC-2 (Adaptive UI Element Discovery):** `explore_interactive_elements` returns detected interactive candidate buttons and text anchors with normalized coordinates from synthetic frames.
3. **AC-3 (Observe-Act-Verify Probing):** `probe_action` dispatches a candidate action, measures visual/textual change, detects state transitions, and records empirical evidence.
4. **AC-4 (Knowledge Persistence & Restart):** Candidate knowledge created by probing persists across storage reloads and is accessible to new sessions.
5. **AC-5 (Isolated Failure Resilience):** A verified skill encountering a single execution error (e.g. temporary focus loss or simulated glitch) retains its `verified` status without demotion.
6. **AC-6 (Suspect State Transition):** Repeated verified postcondition failures ($\ge 2$) transition a skill or transition to `suspect` status rather than instantly deleting or rejecting it.
7. **AC-7 (Checkpoint & Rollback):** Creating a checkpoint, introducing corrupted or misleading knowledge, and triggering a rollback restores the exact pre-checkpoint state.
8. **AC-8 (Gradual Skill Optimization):** `optimize_skill` updates an existing skill with refined steps and bumped version while retaining execution track records.
9. **AC-9 (Cross-Game Concepts Prior Querying):** Generic concepts (`pause_toggle`, `cancel_dialog`, `speed_up`) are queried and utilized without any hardcoded game logic.
10. **AC-10 (Temporary Data Pruning):** `prune_temporary_data` cleanly removes stale screenshot files and truncates action history exceeding retention limits.
11. **AC-11 (Backward Compatibility & Security):** All V1.5 security protections (killswitch, dry-run, anti-cheat blacklist, client bounds) and existing MCP tools remain fully functional.

---

## 7. Feasibility Verdict

**VERDICT: FULLY FEASIBLE (PROCEED TO PHASE B IMPLEMENTATION)**  
The proposed V2 architecture requires no new external dependencies, preserves CPU-first performance, protects all existing security mechanisms, and provides the external AI with clean, generic tools for autonomous discovery and gameplay learning.
