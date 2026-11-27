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

## Verified P0 baseline (2026-11-24)

[Draft PR #8 Windows CI](https://github.com/QuafdaPoet/universal-game-mcp/actions/runs/45386422676/job/138418398086) checked out the integration commit for head `b1beaeb` and passed **311 tests and 50 subtests**, plus the synthetic profile, offline demo, and installed-wheel check. Those results do not establish native game capture, OCR, input, gameplay, or skill promotion.

## P0 release gates

| Priority and status | Work / dependencies | Acceptance evidence |
| --- | --- | --- |
| P0 · Implemented; Windows CI pass | Fail-closed live attachment; no fake window handles or ungrounded center proposal. | Mocked no-match must not initialize the requested game or change target; fixture mode is explicit; empty perception yields no proposal. Real Windows observation checklist below. |
| P0 · Open | Revalidate attached game, HWND/PID and focus immediately before scoped capture or a live input (including when tools supply a different game_id). Probe now rejects missing or changed targets and fixture fallback, and binds input to the observed client. Ordinary capture, OCR and UI state classification use the same focused attachment checks, including rejecting a frame if focus, target or geometry changes during capture. For native UI state classification, an explicit mismatched game ID is rejected before capturing or reading/writing knowledge; an omitted ID is derived from the same attachment snapshot as capture. An explicit fixture stays in the simulation knowledge domain. Remaining: extend equivalent checks to other game-action and observation tools. Verification baseline IDs now expire after 60 seconds or after a successful reattach, geometry/backend change, or evidence-domain change; unknown or stale IDs fail even if the incoming frame could be the same size. Dataset capture requires operator opt-in for native windows, validates tag and ROI before capture, and rejects unbound targets. The local sample sidecar distinguishes fixture, dry-run observation, and live observation. Review the remaining capture surfaces. Depends on fail-closed attach. Each MCP context permits one in-flight action/attachment/rollback sequence and rejects a concurrent one rather than queueing it. Emergency stop bypasses the lock. Native direct click, hover and keys bind to a focused client; a native skill or route entry rejects an unrelated game ID. Remaining: bind every internal step and capture of a skill or route, and validate on a real client. | Missing, stale, minimized, or swapped window cannot emit a desktop image or dispatch input. Two overlapping calls cannot act on the wrong window; emergency stop preempts remaining actions. |
| P0 · Implemented; Windows CI pass | Isolate simulated evidence; separate observations from verified postconditions. | A dry-run cannot mutate live skill stats/graphs/history or satisfy live postconditions. Provenance is visible in responses; an unexplained UI change is not a gameplay rule. |
| P0 · Implemented; Windows CI pass | Namespace learned skills by `(game_id, skill_id)`. | Upgrade a populated original schema without data loss. Same ID in two games is independent across read, write, stats, and rollback. Ambiguous legacy lookups fail explicitly. |
| P0 · Implemented; Windows CI pass | Probe deadline, UI stabilization, and result contract. Depends on evidence provenance and window identity. | Fake-clock tests for delayed transition, noise, unchanged screen, unavailable capture, and rejected dispatch. Generic Escape undo is already disabled; compatibility request returns a reason. Cooperative deadline must distinguish native calls that may block. |
| P0 · Implemented; Windows CI pass | Invalidate both navigator and graph after successful rollback, retaining curated base routes and leaving unrelated caches alone. | `test_checkpoint_rollback_invalidates_cached_navigator`: reproduces stale navigator before fix; after fix verifies stale route gone, seeded route remains, and failed rollback does not evict cache. |
| P0 · Implemented; Windows CI pass | Route replanning must dispatch the new route, not the old iterator. | An unexpected but recognized state is replanned from that state, never from the expected one. Unknown state halts with no next input; retries are bounded. |
| P0 · Implemented; Windows CI pass | Snapshot integrity / transition quarantine. Depends on composite migration and cache fix. | Transactionally capture and recover exact provenance, timestamps, counts and rejected records. Injected error cannot leave a partial game restore; other games unchanged. Rejected or uncertain learned edges cannot be executed as verified routes. |
| P0 · Candidate/gate Windows CI pass; live trial runner open | Revision-specific candidate optimization. Depends on evidence isolation and composite identity. | New skills now start with zero successes and validations. A revision candidate stores its own steps and starts with no inherited outcomes; verified revision remains active. Paired benchmarks and repeated postcondition validations must pass before explicit promotion. Failed trial cannot mutate old revision. |
| P0 · Open | Remaining MCP contract / safety audit. | Unreachable routes now serialize as strict JSON. Still verify selector dispatch fails safely, memory paths stay scoped, limits are bounded, and every public tool matches documented parameters and return semantics. Run full pytest, simulator, Windows wheel install and report evidence separately. |

Back up an existing live SQLite database before first launching schema v5. Use an SQLite backup or stop UGM and copy the database and any WAL sidecars together. Migration preserves existing rows, not knowledge overwritten by earlier globally keyed builds. Do not use an older build on the migrated database. Keep previously contaminated records out of real-game validation; use a fresh live DB for the baseline.

## Skill optimization policy

`optimize_skill` now stages a separate candidate. Ordinary `execute_skill` uses the active definition, not the staged one. Newly learned definitions start at zero validation and success counts. Older data is not rewritten to guess past provenance. A repeated `learn_skill` call cannot replace an already verified program or change its status via a caller-supplied confidence hint. Use `optimize_skill` with `operation="inspect"` and the returned candidate ID plus game and skill IDs to examine a proposal.

Promotion is explicit and guarded by fresh, paired benchmark observations against the unchanged active definition, successful postcondition comparisons, and a measured speed improvement. A candidate starts with no trial credit. The gate requires at least three matched pairs, both execution orders with at most one pair of imbalance, no comparable candidate failures, and at least 10% median paired improvement in each order. Client-provided numbers, manual lifecycle labels, old action history, fixture trials and dry-run outcomes do not meet that gate. There is no MCP producer of trusted live trial receipts yet. A future runner must establish equivalent starting states safely, bind actions to the matching offline game, and record actual postcondition evidence. Do not execute or promote a candidate using a live game simply to make a test pass. Unpromoted candidates and their trials are discarded when their game's checkpoint is restored, so restoring an older active definition cannot reuse stale comparison evidence.

### Headless A/B rehearsal

Run `python scripts/demo_skill_benchmark_sim.py` from a checkout. Pass `--first candidate` to run the opposite order. Each arm gets a separately supplied toy starting state and a single-use in-process rearm token. The demo uses fake-clock action costs, not hardware latency. It can exercise ordering, start-state equality and the postcondition, but its receipt is a different type from live evidence and is never eligible for promotion. It opens no native game window, sends no input, and writes no knowledge database.

A native benchmark runner is still missing. When that becomes possible, a local operator must establish the same offline starting state separately for each arm; a SQLite checkpoint is not a game reset. Never let an MCP caller mark itself ready or submit elapsed times as proof.

## Checkpoint and route policy

Versioned checkpoints preserve exact per-game rows for UI states, transitions, learned skills and selectors, including provenance, timestamps, counters and rejection status. Rollback is atomic and cannot replace another game's rows. Global concepts, action history and route quarantine do not rewind. Old unversioned snapshots remain listed but cannot safely be restored because they omitted fields and rejected transitions; create a new checkpoint before testing rollback.

An observed or explicitly rejected route stays quarantined even after restoring a checkpoint from before the rejection. This applies to both learned and built-in edges with the same exact game, states, action and parameter values. The planner excludes uncertain learned edges and rechecks a queued edge immediately before dispatch. To reject a curated edge before any execution history exists, call `manage_knowledge_state` with `item_type="transition"`, `status="rejected"`, an empty `item_id`, explicit `game_id`, and `transition_identity` containing `from_state`, `to_state`, `action_type` and `params`. Ordinary status changes and checkpoint rollback do not clear quarantine; this change adds no automatic unquarantine workflow.

## Probe behavior

Attach the visible offline game before requesting capture. With no attachment, MCP capture fails rather than grabbing the desktop. Live probes accept a cooperative `timeout_s` from 0.1 to 10 seconds (default 5). Use explicit `x` and `y` for clicks/hovers, and prefer a narrow expected ROI so unrelated animation is less likely to look like a result. The tool distinguishes completed dispatch, dry-run acceptance, blocked actions, unstable observations, and timeouts. Only stable sampled visual differences are reported as observed evidence, not verified gameplay rules; OCR compares pre/post text when time permits. A native operation already in progress cannot be interrupted by this deadline. No generic undo is sent.

Probe frames and probe input are checked against the same focused window, process and client geometry. These checks do not make OS focus changes atomic. Native Windows behavior, multi-monitor/DPI capture and live-game accuracy still need hands-on verification. Other live input paths and concurrent hardware-action serialization remain P0 work.

## Next milestones

1. **V2.1:** Semantic UI observations and persistent selectors; budgets; recovery and external goal-directed exploration. Require repeated distinct evidence before promoting rules. Try HOI4 as a reference, then use fresh profiles for Stellaris and Civilization VI without curated skills.
2. **V2.2:** Versioned state facts for economy, resources, production, research, entities, objectives, events and time. Keep observation confidence, source, timing, and conflicting observations explicit; reconcile with read-only data.
3. **V2.3:** Persistent goals, queue and hierarchical planning support for the external LLM. Test interruption, resumption and recovery before extending unattended sessions.
4. **Ten-game matrix:** HOI4, Stellaris, Civilization VI, CK3, Victoria 3, RimWorld, Factorio, EU IV, Cities: Skylines II, Satisfactory. All are live-unverified by this branch until evidence is recorded for each. Record game version/mod, OS, resolution and UI scale, goal, provisioned knowledge, visible tool inventory, attempts, outcomes, latency/RSS, evidence tier, and blockers. For a zero-knowledge run, use a fresh UGM-only client profile and database, and verify that any separate game-specific state MCP or calculators (for example in Civilization VI or Factorio) are not exposed to the test agent. Do not count fixtures or CI as live results.

## Windows live observation gate (no input)

Run on Windows 10/11 from the repo in an activated Python 3.12 environment with dependencies installed. Close multiplayer sessions. Open the offline game in windowed or borderless mode and show a static menu with a visible label in the current UI language. For example, at the English HOI4 main menu:

```powershell
python scripts/smoke_windows_game_observation.py --process hoi4.exe --title "Hearts of Iron IV" --expect-text "Single Player" --focus-delay 10
```

Switch to the game during the countdown. The script launches a separate dry-run server and temporary database; it tests a deliberately wrong title, then explicitly attaches, checks focused window identity and client-sized PNG capture, and verifies a known OCR marker. It does not call any input tools or save screenshots. The screenshot and OCR use separate captures; a pass in a static menu is not proof that the OCR refers to the returned PNG or that gameplay works. Record the commit, game version, client dimensions, display scaling, game UI scale, window mode and printed result. Once this gate passes, a separate read-only HOI4 scenario can use a paused disposable vanilla save: ask the agent to identify one visible production shortfall and cite the evidence for one proposed adjustment, without dispatching it. On failure, keep the output but do not change safety settings to obtain a pass.

## Synthetic MCP capture memory

Visual verification retains at most ten full-frame baselines and 32 MiB of pixel
buffers total. Expired entries are pruned on insertion or lookup. Newer captures
can evict earlier IDs before the 60-second timeout. A frame larger than the byte
limit can still return a preview but reports `verification_baseline_available=false`.
Preview cropping does not change the coordinate system of a retained baseline.

Run `python -m scripts.benchmark_mcp_capture_memory --width 1920` and then
`python -m scripts.benchmark_mcp_capture_memory --width 3840` in separate
processes. These synthetic calls warm OCR, capture filled fixture frames through
MCP with a small preview ROI, and report cache bytes, ending RSS and OS process
peak RSS/working set in bytes. Peak includes startup and OCR warmup. It is not a
live-game measurement or an end-to-end guarantee of the 250 MB target. The older
direct fixture benchmark does not exercise this cache and reports final RSS only.


RapidOCR now applies its CPU thread setting to the pinned ONNX sessions (four
threads for RapidOCR and two for Lite, capped at available logical CPUs). The
synthetic MCP memory script accepts `--ocr-threads -1`, `--ocr-threads 2`, or
`--ocr-threads 4`; run each arm in a fresh process. Per-phase OS peaks are
cumulative high-water marks, not isolated phase maxima. CI checks that the
expected fixture text and normalized boxes agree across the 4K comparisons.
That does not establish OCR equivalence on other games or languages. Orientation
classifier construction remains open work.

### Bounded OCR calls

Omit `read_text.roi` for full-frame OCR. A supplied region must have four
finite normalized coordinates and positive size, and fit the frame. Invalid
regions fail before capture instead of silently reading the whole frame.
Results still use full-frame coordinates. The native screenshot itself remains
a full client capture; cropping limits OCR inference, not screen capture.

Compare the same simulator screenshot in fresh processes:

```sh
python -m scripts.benchmark_mcp_capture_memory --width 3840 --ocr-threads 2 --ocr-region full
python -m scripts.benchmark_mcp_capture_memory --width 3840 --ocr-threads 2 --ocr-region gold
```

OCR reads the 900×550 fixture or a 153×82 padded crop. The later synthetic
capture loop uses 3840×2160 frames; neither arm runs 4K OCR. Both require exactly
one unambiguous `GOLD:500` item in the expected area of the original image and
stable output within each arm. Do not require identical boxes across arms.
A tighter 135×38 region split this label into overlapping detections in a local
diagnostic, so region selection needs padding and verification rather than a
fixed crop for unknown games.

In three fresh two-thread Linux runs per arm, the median of the per-process
repeated OCR medians was 417 ms full versus 250 ms cropped. Cumulative OS peak
by the end of OCR was 461 MB full versus 471 MB cropped (decimal, medians).
The crop was faster on this fixture but did not lower peak memory. Use bounded
reads for known regions without assuming they meet the 250 MB budget or replace
full-frame discovery. These measurements cover a simulator image, not a game.

## V2.1 preview: goal-grounded UI observations

`explore_interactive_elements({"goal":"production queue","max_results":6})`
returns a short list of visible OCR labels that share words with an objective
provided by the external agent. It does not infer synonyms, gameplay meaning,
actions, or success. Use separate calls for alternative labels. The result has
no click suggestion; an observation ID is local to the result, not a saved
selector. Matches use word count, then raw OCR confidence, with a stable
geometry tie-break. The default limit is 6 (maximum 12); one scan runs, with
no hardware input or knowledge writes. This limits returned context size,
not OCR inference memory or the total number of calls. No matching label
yields an empty list. Legacy calls without a goal keep their candidate shape.

The source distinguishes explicit fixture, native observation in dry-run, and
ordinary live observation. Locator and exploration frames are scoped to the
attached game; an attachment or backend change invalidates the result.
Automatic route discovery declines an unknown or ambiguous initial state
rather than routing from a guess. This preview does not mark P0 or V2.1 complete.

| Remaining V2.1 dependency | Acceptance evidence |
| --- | --- |
| Close native input/capture P0 gates | Hardware sequence serialization with independent stop, bound target and postconditions; Windows real-client observation before safe disposable-action trials. |
| Evidence-backed persistence and selectors | Re-observe a recipe under matched game and state, require independent valid receipts before promotion, separate simulations, retain contradictory evidence, and decline ambiguous or stale selectors. |
| Adaptive recognition and bounded recovery | Unknown or ambiguous states emit bounded observations only; recovery can use validated routes but never sends blind reset keys. Exercise shifted layout and blocked recovery fixtures. |
| Exploration budget and cross-game concepts | Budget persists for a bounded exploration session; concept transfer is candidate-only and cannot carry over game IDs, coordinates, or verified status. |
| Zero-knowledge gate | Fresh test profiles on Windows with no curated Stellaris/Civ VI skills; real games, fixtures and mock results reported separately. |

### Native compound actions

The MCP `navigate_to` and `execute_skill` tools bind one focused native
client, game ID, attachment revision and capture backend at entry. Steps
recheck that scope before sending input or using a frame; they never refocus
a different window. An interruption after dispatch retains the fact that a
step ran, but does not add a successful transition or credit a skill result.
Multi-step dry-run may still inspect the real attached client and requires
the same native scope; use the explicit offline fixture for headless work.
Fixture behavior and storage remain separate. These checks are cooperative:
they cannot make desktop input dispatch atomic with OS focus changes or
replace Windows real-game testing.

### Opt-in exploration scan quota

Start a read-only goal session with
`explore_interactive_elements({"goal":"production","session_scan_budget":4})`.
Use the returned `session.id` as `exploration_session_id` for later goal scans.
Each accepted attempt consumes one scan before capture, even if OCR fails.
`session.scans_remaining` indicates when to stop. Simultaneous continuation
of the same session is refused. The same attachment and backend must remain
in use. A session expires for new scans after ten minutes; the server holds
at most eight session records and accepts at most 16 scans each. Sessions
do not survive a process restart and never write gameplay evidence.
This is a cooperative budget for clients using that ID, not a global limit
on legacy calls or starting new sessions, nor a hard OCR wall-clock timeout.

### Preview: evidence ledger for OCR labels

Schema v6 adds an isolated ledger for (game, known state, selector ID).
Legacy selector rows are not upgraded to evidence. A recipe is an exact
normalized text label, not saved coordinates or a claim that the label is
interactive. Matching declines missing, competing or unreadable labels.
Three distinct clean frame digests from at least two attachment tokens
are required for `repeat_observed`; an informative absent or ambiguous
sample resets that run, and old evidence expires after 30 days. The ledger
retains contradictions and duplicate identities until the game/state
memory is cleared. At 128 receipts, it enters `needs_review` and requires
recipe review or versioning. Rolling back a legacy checkpoint clears this
ledger for the selected game rather than reviving unprovable evidence.

This draft includes the matcher, storage, and migration, **not** a native
receipt producer or MCP persistence feature. Tests inject typed receipts
with fake data; they are not recordings of a real game. The next gate is
a server-issued receipt from a fresh scoped capture of a known screen,
with fixture/dry-run/default-off isolation. A repeated OCR observation
does not grant input authority, even after that gate.

#### Native observation adapter

`observe_ui_selector({"selector_id":"production","label":"Production"})`
does one fresh **read-only** native observation. It requires a known state
from the same frame and returns the current exact-match box, never a cached
click location. For an explicit observation to update the ledger, start UGM
with `UGM_SEMANTIC_SELECTOR_LEARNING=1` and set `record:true`. Recording
refuses fixture, mock OCR, simulation storage, and dry-run. Game, state,
frame digest and attachment token all come from the server. Scope is checked
again inside the transaction. One new frame per call is eligible; recapturing
identical pixels does not add evidence. This API never sends input, does not
infer game rules, and intentionally does not resolve unknown UI states yet.

This adapter is tested via the real MCP call path with injected captures
and OCR. Native Windows capture and real games still need validation. The
selector still requires a unique match on the fresh image, even if the
ledger says `repeat_observed`. The smoke test and the general 250 MB peak
target are separate gates.

### Unknown-state triage

When classification is unknown, `triage_ui_state` can inspect two fresh
same-attachment snapshots. A small grayscale thumbnail must agree and at
least two unique OCR labels must recur at approximately the same location.
At most six normalized labels are returned; numeric counters, duplicate
labels and low-confidence matches do not qualify. The labels are untrusted
observations, not instructions. Even a stable result keeps state
`unknown`: no input, persistent state, coordinates, verified route, or
selector is created. An attachment/backend change fails the call. The
normal navigation gate remains in force. This is bounded guidance, not
automatic recovery or proof of generalization to a real game.

### Candidate-only transfer between games

`propose_concept_transfer` checks a repeated source selector receipt for a
reviewed English concept such as pause, cancel, or game speed. It then
looks for a unique exact cue in one fresh capture of a **different** attached
native game. Source receipts must be current and uncontested. Unfamiliar,
weak, or competing responses do not create a candidate. The result is an
OCR cue, never proof that either game's control behaves the same. No keys,
coordinates, input, route parameters, legacy concept descriptions, or
verified state are transferred. The legacy concept query remains unchanged
for compatibility. No game-specific skill pack or new persistent record
is required for this candidate check.
