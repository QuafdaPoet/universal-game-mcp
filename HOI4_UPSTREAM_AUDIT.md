# Upstream Audit: HOI4 Live Tools

**Audited Repository:** [klimPaskov/HOI4-Live-Tools](https://github.com/klimPaskov/HOI4-Live-Tools)  
**Pinned Upstream Commit SHA:** `6b91a2a43edfeab60a415ec2ddd9b71d215b8a56`  
**License:** Apache License 2.0 (with select MIT-derived Clausewitz-MCP components in native module)  
**Auditor:** Universal Game MCP (UGM) Architecture & Engineering Team  
**Date:** October 2026  

---

## 1. Executive Summary

An in-depth architectural audit of `HOI4-Live-Tools` was conducted to identify, extract, and adapt valuable capabilities into `Universal Game MCP (UGM)`. The audit evaluated campaign knowledge playbooks, desktop perception and OCR techniques, executable gameplay workflows, game state telemetry, testing patterns, and low-level native runtime components.

### Core Architectural Decisions:
1. **Primary Infrastructure:** UGM remains the single, primary MCP server and execution runtime. HOI4 Live Tools is **not** vendored wholesale, nor is a second MCP server deployed.
2. **Strategy vs. UI Decoupling:** Campaign strategy heuristics are strictly decoupled from executable UI primitives. Strategy informs *what* to achieve; UGM selectors, navigation graphs, and Game Skills govern *how* to interact with the game safely.
3. **Windows-First Verification:** Upstream native memory scanning (`native/`) is strictly Linux-oriented (using `/proc/<pid>/mem`) and has no validated Windows memory layout. Direct memory injection was **rejected** from the critical path.
4. **Selective Adaptation:** High-value perception algorithms (morphological differential tooltip detection, paper-mask modal popup segmentation, resilient OCR date parsing with digit confusion recovery, and hazard-stripe pause detection) were extracted, optimized, and natively integrated into `ugm/adapters/hoi4/perception.py`.

---

## 2. Component-by-Component Evaluation

### A. Campaign Knowledge Playbook (High Priority)
* **Source:** `skills/hoi4-campaign-playbook/` (`SKILL.md`, `rules-and-modes.md`, `operating-loop.md`, `strategy.md`, `germany-fair.md`, `china-fair.md`, `poland-meta.md`).
* **Evaluation:** Highly structured, domain-accurate heuristics covering economic compounding (2.5–3 year civilian factory payback horizon), research priorities (ahead-of-time penalties, industry/computing focus), stockpile coverage limits, and operating cadence (daily background risk checks, pausing only for player choices).
* **Adaptation:** Extracted and normalized into clean JSON schemas under `game_skills/hoi4/knowledge/`:
  - `meta.json`: Versioning, patch notes, attribution manifest.
  - `rules_and_modes.json`: Explicit FAIR rules (bans on space marines, order-66 exploits) and META guidelines.
  - `operating_loop.json`: Daily cadence, standing plan schemas, emergency stop criteria.
  - `strategy_core.json`: Economy, trade repair order, division doctrine design, force architecture.
  - `countries/germany_fair.json`: Phase 1–5 progression, Anschluss, Danzig, Barbarossa planning.
  - `countries/china_fair.json`: Yellow/Yangtze river defense in depth, warlord subjugation, port defense.
  - `countries/poland_meta.json`: Monarchist Baltic union, Romanian bridgehead, mobile encirclement.
* **On-Demand Retrieval:** Exposed via `HOI4KnowledgeManager` and MCP tool `hoi4_get_knowledge(topic, subtopic)` so agents load only relevant sections, preventing context window bloat.

### B. Perception and Computer Vision (High Priority)
* **Source:** `hoi4_live/ocr/`, `hoi4_live/perception/`, `hoi4_live/desktop/`.
* **Evaluation:**
  1. *Tooltip Box Detection:* Upstream differential analysis computes before-and-after frame deltas, applies morphological closing (`cv2.morphologyEx`), and filters connected components by aspect ratio, density, and distance to cursor. Excellent technique.
  2. *Popup Classification:* Upstream segments warm parchment (country events) and grey paper (news events) using color range thresholds and morphological closing, identifying modal windows and response buttons.
  3. *Date Parsing:* Upstream utilizes digit-fix translation tables to repair OCR confusions (`l` -> `1`, `O` -> `0`, `S` -> `5`).
  4. *Pause & Speed:* Analyzes green speed icon presence and date box hazard stripe patterns.
* **Adaptation:** All four algorithms were refactored into `ugm/adapters/hoi4/perception.py` and wrapped by `HOI4Adapter`.

### C. Executable Gameplay Workflows & Tools Maturity Matrix
Each upstream tool family documented in `docs/tools.md` was inspected and classified according to actual operational maturity:

| Upstream Tool Family | Claimed Functionality | Maturity Classification | UGM Adaptation Path |
| :--- | :--- | :--- | :--- |
| `hoi4_status` | Read game speed, date, pause state, screen mode | Verified with Fixture & Live | Implemented in `HOI4Adapter.parse_topbar()` and `hoi4_read_topbar` |
| `hoi4_screen` | Capture window client rect | Verified Live | Natively covered by UGM `CaptureManager` & `BetterCam` |
| `hoi4_hover` | Hover over coordinates/selectors | Implemented | Natively covered by UGM `hover_target` & `read_tooltip` |
| `hoi4_ocr` | OCR text in bounding box | Verified Live | Natively covered by UGM `RapidOCREngine` with caching |
| `hoi4_time` | Pause, unpause, set speed | Verified Live | Converted to native UGM Skills (`pause_unpause`, `set_speed`) |
| `hoi4_focus` | Open focus tree, select focus | Implemented / Partial Fixture | Converted to UGM Skills (`open_national_focus`, `select_national_focus`) |
| `hoi4_research` | Inspect slots, start research | Implemented / Partial Fixture | Converted to UGM Skills (`inspect_research_slots`, `start_research`) |
| `hoi4_production` | Inspect lines, allocate factories | Implemented / Partial Fixture | Converted to UGM Skills (`open_production`, `adjust_factory_allocation`) |
| `hoi4_construction`| Inspect queue, add building | Implemented / Partial Fixture | Converted to UGM Skills (`open_construction`, `queue_construction`) |
| `hoi4_politics` | Open politics, appoint minister | Implemented | Converted to UGM Skills (`open_politics`, `national_focus_slot`) |
| `hoi4_army` | Select army, assign general | Implemented | Converted to UGM Skills (`select_army`, `assign_commander`) |
| `hoi4_orders` | Draw frontline, offensive line | Documentation / Experimental | Documented in Playbook; Tier C inspection skill created |
| `hoi4_diplomacy` | Open diplomacy, justify war | Implemented / Unverified | Navigation node & edge registered; manual skill deferred |
| `hoi4_advance` | Autonomous multi-day loop | Proposal Only | Orchestrated via UGM Autonomous Engine & standing plans |

### D. Game State & Telemetry
* **Source:** `hoi4_live/state/`, companion mod hooks.
* **Evaluation:** Telemetry mod hooks require modifying game files or user playsets, posing anti-cheat and stability risks.
* **Decision:** Telemetry companion mods remain **disabled by default**. UGM relies on non-invasive OCR, template matching, and read-only save file / asset inspection via `GameAssetIndexer`.

### E. Native Rust & Clausewitz-MCP (Research Only)
* **Source:** `native/`, `third_party/clausewitz_mcp/`.
* **Evaluation:** Upstream native code targets Linux `/proc/<pid>/mem` virtual address mapping. It does not provide validated PE offset scanning or Windows 10/11 handle duplication.
* **Decision:** Strictly kept outside the critical path. No code injection, DLL hijacking, or executable patching is performed.

---

## 3. Provenance & Attribution Summary

* **Upstream Project:** `HOI4-Live-Tools`
* **Upstream Author:** Klim Pashkov (`klimPaskov`)
* **Upstream Commit:** `6b91a2a43edfeab60a415ec2ddd9b71d215b8a56`
* **License:** Apache License 2.0
* **Provenance Manifest:** Recorded in `HOI4_PROVENANCE.md` and `game_skills/hoi4/knowledge/meta.json`.
