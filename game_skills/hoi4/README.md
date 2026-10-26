# Hearts of Iron IV (HOI4) Reference Game Skills Pack

## Notice & Disclaimer

> [!NOTE]
> Hearts of Iron IV is used here as the **initial reference game pack** to validate the Universal Game MCP V1 perception, selector, and skill pipeline.
> The selectors and skills defined in this pack are structured reference examples. They have been verified against synthetic and fixture screenshot datasets (`tests/fixtures/hoi4_topbar.png`, `tests/fixtures/hoi4_dialog.png`). They are **not** represented as exhaustive or verified across all mods, game patches, or live multiplayer sessions of the real game.

## UI Selectors

| Selector ID | Type | Description | Disambiguation Strategy |
|-------------|------|-------------|-------------------------|
| `topbar_factories` | Composite | Factory counter and factory icon | Template match + OCR digits |
| `political_power` | Composite | Political Power icon + number | Template match + OCR digits |
| `pause_button` | Icon | Topbar pause/play status button | Multi-scale template matching |
| `army_group_leader` | Composite | Army group commander portrait badge | Template match + tooltip check |
| `recruit_tab` | Composite | Division deployment tab button | Template match + tab OCR text |

## Atomic Skills

1. **`pause_unpause`**:
   - Dispatches space key or clicks pause button.
   - Verifies visual state difference on the date/pause widget.
2. **`open_production`**:
   - Presses production hotkey (`y`) or clicks production tab.
   - Verifies production panel opens via visual diff and header OCR.
3. **`select_army_group`**:
   - Targets the army group commander icon at the bottom theater area.
   - Validates tooltip to ensure commander identity before clicking.
   - Verifies army selection ring / panel change.
4. **`increase_speed`**:
   - Dispatches `num+` to increase game speed.
   - Verifies speed indicator notch change.

## Verification Workflow

When executing any skill:
1. Target candidate is detected via template matching and/or OCR within the scoped ROI.
2. If multiple candidates have competing confidence scores (>95% similarity ratio), the selector is flagged as **ambiguous** and the click is aborted (or tooltip hover is triggered).
3. The click is dispatched via DirectInput only after unambiguous resolution.
4. The post-action frame is captured and compared with the pre-action frame.
5. The result returns one of `success`, `failed`, `ambiguous`, or `not_found` with coordinates and evidence.
