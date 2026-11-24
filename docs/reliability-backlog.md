# UGM reliability and validation backlog

The V2.1 work starts after P0 gates pass. A game-independent core exposes evidence to one external LLM; it does not supply its own gameplay model. Keep game-specific rules in optional inputs, retain read-only game data, and do not support online or multiplayer automation.

## Verified starting point (2026-11-24)

| Ref | Head | Comparison with main |
| --- | --- | --- |
| `main` | `fa05297` | 34 tools, 1.5.0 |
| `fix/windows-ci-root-causes` | `8b922fb` | +1 / −0 |
| `release/ugm-v2-readiness` | `110c348` | +2 / −0; 34 tools, 2.0.0.dev0 |
| `feat/hoi4-prebuilt-integration` | `44cfa72` | +5 / −6; open [PR #2](https://github.com/QuafdaPoet/universal-game-mcp/pull/2), not part of this baseline |

[Windows readiness CI](https://github.com/QuafdaPoet/universal-game-mcp/actions/runs/45359835996/job/138326772032) at `110c348`: 182 tests and 50 subtests passed, including subprocess MCP validation. The synthetic benchmark, offline simulator, and installed-wheel asset check also passed. The wheel check used preinstalled dependencies, and the CI capture fallback was not evidence of live capture. The existing main and HOI4 PR jobs had failures; do not roll their results into readiness. Neither a Windows runner nor a fixture demonstrates gameplay on a real game.

## P0 release gates

| Priority and status | Work / dependencies | Acceptance evidence |
| --- | --- | --- |
| P0 · Implemented; Windows pending | Fail-closed live attachment; no fake window handles or ungrounded center proposal. | Mocked no-match must not initialize the requested game or change target; fixture mode is explicit; empty perception yields no proposal. Real Windows observation checklist below. |
| P0 · Open | Revalidate attached game, HWND/PID and focus immediately before scoped capture or a live input (including when tools supply a different game_id). Probe now rejects missing or changed targets and fixture fallback, and binds input to the observed client. Remaining: apply equivalent focus/identity and frame provenance checks to other game-action and observation tools. Depends on fail-closed attach. Enforce one in-flight hardware sequence per target; stop independent of that queue. | Missing, stale, minimized, or swapped window cannot emit a desktop image or dispatch input. Two overlapping calls cannot act on the wrong window; emergency stop preempts remaining actions. |
| P0 · Implemented; Windows pending | Isolate simulated evidence; separate observations from verified postconditions. | A dry-run cannot mutate live skill stats/graphs/history or satisfy live postconditions. Provenance is visible in responses; an unexplained UI change is not a gameplay rule. |
| P0 · Implemented; Windows pending | Namespace learned skills by `(game_id, skill_id)`. | Upgrade a populated original schema without data loss. Same ID in two games is independent across read, write, stats, and rollback. Ambiguous legacy lookups fail explicitly. |
| P0 · Implemented; Windows pending | Probe deadline, UI stabilization, and result contract. Depends on evidence provenance and window identity. | Fake-clock tests for delayed transition, noise, unchanged screen, unavailable capture, and rejected dispatch. Generic Escape undo is already disabled; compatibility request returns a reason. Cooperative deadline must distinguish native calls that may block. |
| P0 · Implemented; Windows pending | Invalidate both navigator and graph after successful rollback, retaining curated base routes and leaving unrelated caches alone. | `test_checkpoint_rollback_invalidates_cached_navigator`: reproduces stale navigator before fix; after fix verifies stale route gone, seeded route remains, and failed rollback does not evict cache. |
| P0 · Implemented; Windows pending | Route replanning must dispatch the new route, not the old iterator. | An unexpected but recognized state is replanned from that state, never from the expected one. Unknown state halts with no next input; retries are bounded. |
| P0 · Open | Snapshot integrity / transition quarantine. Depends on composite migration and cache fix. | Transactionally capture and recover exact provenance, timestamps, counts and rejected records. Injected error cannot leave a partial game restore; other games unchanged. Rejected or uncertain learned edges cannot be executed as verified routes. |
| P0 · Open | Revision-specific candidate optimization. Depends on evidence isolation and composite identity. | New skills must not start with fictitious successes or validations. A revision candidate stores its own steps and starts with no inherited outcomes; verified revision remains active. Paired benchmarks and repeated postcondition validations must pass before explicit promotion. Failed trial cannot mutate old revision. |
| P0 · Open | Remaining MCP contract / safety audit. | Unreachable routes now serialize as strict JSON. Still verify selector dispatch fails safely, memory paths stay scoped, limits are bounded, and every public tool matches documented parameters and return semantics. Run full pytest, simulator, Windows wheel install and report evidence separately. |

Back up an existing live SQLite database before first launching schema v3. Use an SQLite backup or stop UGM and copy the database and any WAL sidecars together. Schema v3 preserves existing rows, not knowledge overwritten by earlier globally keyed builds. Do not use an older build on the migrated database. Keep previously contaminated records out of real-game validation; use a fresh live DB for the baseline.

## Probe behavior

Attach the visible offline game before requesting capture. With no attachment, MCP capture fails rather than grabbing the desktop. Live probes accept a cooperative `timeout_s` from 0.1 to 10 seconds (default 5). Use explicit `x` and `y` for clicks/hovers, and prefer a narrow expected ROI so unrelated animation is less likely to look like a result. The tool distinguishes completed dispatch, dry-run acceptance, blocked actions, unstable observations, and timeouts. Only stable sampled visual differences are reported as observed evidence, not verified gameplay rules; OCR compares pre/post text when time permits. A native operation already in progress cannot be interrupted by this deadline. No generic undo is sent.

Probe frames and probe input are checked against the same focused window, process and client geometry. These checks do not make OS focus changes atomic. Native Windows behavior, multi-monitor/DPI capture and live-game accuracy still need hands-on verification. Other live input paths and concurrent hardware-action serialization remain P0 work.

## Next milestones

1. **V2.1:** Semantic UI observations and persistent selectors; budgets; recovery and external goal-directed exploration. Require repeated distinct evidence before promoting rules. Try HOI4 as a reference, then use fresh profiles for Stellaris and Civilization VI without curated skills.
2. **V2.2:** Versioned state facts for economy, resources, production, research, entities, objectives, events and time. Keep observation confidence, source, timing, and conflicting observations explicit; reconcile with read-only data.
3. **V2.3:** Persistent goals, queue and hierarchical planning support for the external LLM. Test interruption, resumption and recovery before extending unattended sessions.
4. **Ten-game matrix:** HOI4, Stellaris, Civilization VI, CK3, Victoria 3, RimWorld, Factorio, EU IV, Cities: Skylines II, Satisfactory. All are live-unverified by this branch until evidence is recorded for each. Record game version/mod, OS, resolution and UI scale, goal, provisioned knowledge, attempts, outcomes, latency/RSS, evidence tier, and blockers. Do not count fixtures or CI as live results.

## Windows live observation gate (no input)

Run on Windows 10/11 from the repo in an activated Python 3.12 environment with dependencies installed. Close multiplayer sessions. Open the offline game in windowed or borderless mode and show a static menu with a visible label in the current UI language. For example, at the English HOI4 main menu:

```powershell
python scripts/smoke_windows_game_observation.py --process hoi4.exe --title "Hearts of Iron IV" --expect-text "Single Player" --focus-delay 10
```

Switch to the game during the countdown. The script launches a separate dry-run server and temporary database; it tests a deliberately wrong title, then explicitly attaches, checks focused window identity and client-sized PNG capture, and verifies a known OCR marker. It does not call any input tools or save screenshots. Capture/OCR PASS is not input or gameplay validation; report the output together with game version, resolution, UI scale and window mode. On failure, keep the output but do not change safety settings to obtain a pass.
