# Game Skills Architecture (UGM V1)

## Overview

The `game_skills/` directory provides a modular, game-independent structure for defining perception selectors, UI templates, keybindings, and macro skills for individual games without coupling them directly into the UGM Core runtime.

Hearts of Iron IV (`hoi4`) serves as the first reference implementation. **HOI4 is an independent reference game pack, not a dependency of UGM Core.**

Each Game Skills pack can be loaded, validated, and managed completely independently.

## Directory Structure

```text
game_skills/
├── README.md                      # Architecture and specifications (this document)
├── _template/                     # Starter template for authoring new game packs
│   ├── manifest.json              # Versioned manifest schema template
│   ├── skills/                    # Atomic skills and action macros
│   │   ├── .gitkeep
│   │   └── example_skill.json
│   ├── selectors/                 # Visual and OCR target selectors
│   │   ├── .gitkeep
│   │   └── example_selector.json
│   └── templates/                 # Image templates for CV matching (.png)
│       └── .gitkeep
└── hoi4/                          # Hearts of Iron IV reference skill pack
    ├── manifest.json              # Game metadata, resolution profiles, skill index
    ├── README.md                  # HOI4-specific skill documentation & caveats
    ├── skills/                    # HOI4 atomic skills
    │   ├── pause_unpause.json
    │   ├── open_production.json
    │   ├── select_army_group.json
    │   └── increase_speed.json
    ├── selectors/                 # HOI4 UI target selectors
    │   ├── topbar_factories.json
    │   ├── political_power.json
    │   ├── pause_button.json
    │   ├── army_group_leader.json
    │   └── recruit_tab.json
    ├── templates/                 # Reference icon templates (civilian factory, PP, etc.)
    │   ├── civilian_factory.png
    │   ├── military_factory.png
    │   ├── pause_btn.png
    │   ├── political_power.png
    │   └── stability.png
    └── tests/                     # Validation fixtures and schema checks
        └── README.md
```

## Manifest Schema (Version 1.0.0)

Every game pack must provide a `manifest.json` at its root conforming to the minimal versioned schema:

```json
{
  "$schema": "https://raw.githubusercontent.com/universal-game-mcp/schemas/v1/game-skill-pack.json",
  "schema_version": "1.0.0",
  "game_id": "hoi4",
  "name": "Hearts of Iron IV Reference Pack",
  "version": "1.0.0",
  "author": "Universal Game MCP",
  "description": "Perception selectors, UI templates, and atomic skills for Hearts of Iron IV.",
  "game_metadata": {
    "process_names": ["hoi4.exe"],
    "window_titles": ["Hearts of Iron IV"],
    "base_resolution": [1920, 1080],
    "supported_resolutions": ["1920x1080", "2560x1440"],
    "dpi_aware": true
  },
  "selectors_path": "selectors",
  "skills_path": "skills",
  "templates_path": "templates",
  "selectors": [
    "topbar_factories",
    "political_power",
    "pause_button",
    "army_group_leader",
    "recruit_tab"
  ],
  "skills": [
    "pause_unpause",
    "open_production",
    "select_army_group",
    "increase_speed"
  ]
}
```

## Selector Schema

Selectors define how to identify UI components (icons, text, buttons, unit badges) dynamically on screen without hardcoding static screen coordinates:

- `selector_id`: Unique identifier (e.g., `army_group_leader`).
- `type`: `icon`, `text`, or `composite` (combines template matching + OCR text confirmation).
- `search_roi`: Normalized search region `[x, y, width, height]` (0.0 to 1.0).
- `template`: Template matching parameters (file, threshold, multi-scale flag).
- `text_criteria`: Optional regex pattern or exact string for OCR verification.
- `hover_verification`: Optional tooltip hover check before confirming target.
- `ambiguity_handling`: Rules for rejecting or escalating ambiguous candidates.

## Skill Schema

Skills define atomic actions or sequences paired with strict post-verification:

- `skill_id`: Unique identifier (e.g., `pause_unpause`).
- `preconditions`: Required state before execution (e.g., `window_focused: true`).
- `steps`: Ordered input actions (`click_target`, `press_keys`, `hover_target`).
- `post_verification`: Verification conditions (e.g., visual diff ratio or OCR expected text).
- `timeout_ms`: Maximum execution timeout.

## Scope Boundaries (V1 vs V1.5)

- **V1 (Current)**:
  - Declarative manifests, selectors, and skills.
  - Independent loading and validation via Python loader.
  - Safe execution with unambiguous target selection, hover verification, and visual diff checks.
- **Out of Scope for V1 (Reserved for V1.5)**:
  - Persistent online learning and memory.
  - Autonomous skill discovery and reinforcement training.
  - Navigation graphs and automated menu traversal.
  - Cross-model skill sharing networks.
