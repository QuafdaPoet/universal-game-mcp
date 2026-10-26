# Game Skills Architecture (UGM V1.5)

## Overview

The `game_skills/` directory provides a modular, game-independent structure for defining perception selectors, UI templates, keybindings, and macro skills for individual games without coupling them directly into the UGM Core runtime.

Hearts of Iron IV (`hoi4`) serves as the reference implementation. **HOI4 is an independent reference game pack, not a hardcoded dependency of UGM Core.**

In UGM V1.5, Game Skills are **fully executable workflows**, not merely declarative reference files. They are executed through `SkillExecutionEngine` and expanded procedurally through `SkillLearner`.

---

## Directory Structure

```text
game_skills/
├── README.md                      # Architecture and specifications (this document)
├── _template/                     # Starter template for authoring new game packs
│   ├── manifest.json              # Versioned manifest schema template
│   ├── skills/                    # Atomic skills and action macros
│   │   └── example_skill.json
│   ├── selectors/                 # Visual and OCR target selectors
│   │   └── example_selector.json
│   └── templates/                 # Image templates for CV matching (.png)
└── hoi4/                          # Hearts of Iron IV reference skill pack
    ├── manifest.json              # Game metadata, resolution profiles, skill index
    ├── README.md                  # HOI4-specific skill documentation & caveats
    ├── skills/                    # Curated skills
    │   ├── pause_unpause.json     # Spacebar toggle + red PAUSED indicator check
    │   ├── open_production.json    # Hotkey 'y' + 'Production' drawer OCR check
    │   ├── select_army_group.json  # Leader portrait locate & click + bottom bar check
    │   └── increase_speed.json    # Numpad + key + tick speed diff check
    ├── selectors/                 # UI target selectors
    │   ├── topbar_factories.json
    │   ├── political_power.json
    │   ├── pause_button.json
    │   ├── army_group_leader.json
    │   └── recruit_tab.json
    └── templates/                 # Reference icon templates (civilian factory, PP, etc.)
        ├── civilian_factory.png
        ├── military_factory.png
        ├── pause_btn.png
        ├── political_power.png
        └── stability.png
```

---

## Curated Skills vs. Learned Persistent Skills

UGM V1.5 strictly differentiates between two tiers of skills:

| Dimension | Curated Skills (`game_skills/`) | Learned Persistent Skills (`SQLite`) |
|---|---|---|
| **Storage** | Version-controlled JSON files in repository | Local SQLite database (`learned_skills` table) |
| **Author** | Game pack maintainers / developers | External AI agents via `learn_skill` MCP tool |
| **Protection** | Read-only; cannot be overwritten by AI | Can be inspected, updated, validated, or deleted |
| **Lifecycle** | Shipped with repository releases | Starts as `candidate`; promoted to `verified` after ≥ 3 tests |
| **Lookup Order** | Priority 1 (checked first in engine) | Priority 2 (checked if not in curated packs) |

---

## Skill Execution Engine (`ugm.skills.engine`)

Skills are executed safely by `SkillExecutionEngine`. Arbitrary Python execution or shell script execution is strictly disallowed.

### Allowlisted Action Primitives
1. `press_keys`: Sends DirectInput keystrokes (`keys`, `duration_ms`).
2. `click_target`: Clicks normalized screen coordinates `(x, y)` with button and click count.
3. `locate_and_click`: Resolves dynamic selector first, then clicks detected center.
4. `click_selector`: Alias for `locate_and_click`.
5. `hover`: Moves mouse to `(x, y)` and dwells for `duration_ms`.
6. `wait`: Pauses execution for `duration_ms` with safety check.
7. `verify_state`: Compares pre/post frame visual difference and optional OCR text.

### Execution Workflow
1. **Emergency Stop Check**: Verifies global killswitch is inactive.
2. **Skill Resolution**: Resolves skill from curated packs or persistent memory.
3. **Primitive Validation**: Rejects skills containing disallowed primitives.
4. **Prerequisite Completeness Check**: Verifies referenced template files exist on disk.
5. **Preconditions**: Checks game window focus and active attachment.
6. **Pre-Action Baseline Capture**: Captures baseline frame for verification.
7. **Step Dispatch**: Dispatches sequential allowlisted primitives with parameter binding.
8. **Postcondition Verification**: Captures post-action frame and validates visual diff ratio or OCR expected text.
9. **History Logging**: Records action outcome into persistent SQLite `action_history`.

---

## Self-Learning Pipeline (`ugm.skills.learner`)

External AI agents can discover and register new workflows via the `learn_skill` MCP tool:

```
Observe (Frame) ──> Identify (State) ──> Plan (Primitives) ──> Act (Engine) ──> Verify (Postcondition) ──> Learn (Candidate) ──> Reuse (Verified)
```

- **Validation Track Record**: Each learned skill tracks `validation_count`, `success_count`, `fail_count`, and `confidence`.
- **Procedural Promotion**:
  - `status = 'candidate'`: Newly recorded skill.
  - `status = 'verified'`: Promoted after ≥ 3 successful validations with confidence ≥ 0.85.
  - `status = 'rejected'`: Demoted or flagged if failure rate exceeds 50%.
- **Curated Protection**: If `learn_skill` attempts to register a skill ID matching an existing curated skill, it is explicitly rejected with `SafetyViolationError`.
