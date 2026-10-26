# HOI4 Integration Provenance & Attribution

This document records the upstream origin, licensing, and adaptation provenance for Hearts of Iron IV capabilities integrated into Universal Game MCP (UGM).

---

## 1. Upstream Source Attribution

* **Source Repository:** `https://github.com/klimPaskov/HOI4-Live-Tools`
* **Original Author:** Klim Pashkov (`klimPaskov`)
* **Pinned Upstream Commit:** `6b91a2a43edfeab60a415ec2ddd9b71d215b8a56`
* **Upstream License:** Apache License 2.0 (with select MIT-derived Clausewitz-MCP components in native module)

---

## 2. Component Lineage & Modification Record

| Component | Source Path in Upstream | Destination Path in UGM | Modification Nature | License |
| :--- | :--- | :--- | :--- | :--- |
| **Campaign Playbook Knowledge** | `skills/hoi4-campaign-playbook/` | `game_skills/hoi4/knowledge/` | Normalized from markdown into structured JSON schemas (`meta.json`, `rules_and_modes.json`, `operating_loop.json`, `strategy_core.json`, `countries/*.json`). Integrated on-demand retrieval manager. | Apache-2.0 |
| **Differential Tooltip Detection** | `hoi4_live/ocr/tooltips.py`, `find_box()` | `ugm/adapters/hoi4/perception.py` (`detect_tooltip_box`) | Refactored morphological closing and connected component filtering to operate on before/after frame deltas. | Apache-2.0 |
| **Modal Popup Detection** | `hoi4_live/ocr/popup.py`, `_paper_masks()` | `ugm/adapters/hoi4/perception.py` (`detect_popups`) | Adapted parchment and grey paper mask segmentation and contour scaling. | Apache-2.0 |
| **Resilient Date Parsing** | `hoi4_live/perception/date.py` | `ugm/adapters/hoi4/perception.py` (`parse_hoi4_date`) | Extracted regex and digit substitution tables (`_DIGIT_FIX`) for OCR confusion resilience. | Apache-2.0 |
| **Hazard-Stripe Pause Analysis** | `hoi4_live/desktop/speed.py` | `ugm/adapters/hoi4/perception.py` (`detect_pause_state`, `detect_speed_level`) | Adapted hazard stripe RGB variance and speed segment lit analysis. | Apache-2.0 |
| **Environment Discovery** | *Newly authored* | `ugm/adapters/hoi4/environment.py` | Built from scratch for Windows Steam library detection, process inspection, and OCR readiness. | Apache-2.0 |
| **UI State Catalog (12 states)** | *Newly authored* | `ugm/navigation/recognizer.py` | Extended UGM multi-signal classifier for HOI4 panels. | Apache-2.0 |
| **HOI4 Game Skill Pack** | *Newly authored* | `game_skills/hoi4/` (15 selectors, 25 skills) | Built natively conforming to UGM GameSkillPack schema. | Apache-2.0 |

---

## 3. Applicable Upstream License Text

```text
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   Copyright 2026 Klim Pashkov

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```
