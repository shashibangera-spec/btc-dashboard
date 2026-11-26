# Independent BTC AMT Research Engine — v1.0

## Scope
A read-only research replay that reads raw Binance BTCUSDT spot 30-minute OHLCV candles from Supabase `public.binance_ohlc_history`. It does not read Pine outputs or `amt_snapshots` as calculation inputs, and it does not write to Supabase.

## Session and data rules
- Auction session: 05:30 IST to 05:30 IST (48 x 30-minute candles).
- Only completed candles are accepted.
- Duplicate timestamps and invalid OHLC ranges are rejected.
- A missing 30-minute candle breaks consecutive confirmation counters.
- A prior profile is eligible only when the previous session contains exactly 48 contiguous candles with the expected session boundaries.
- Partial current sessions may be replayed, but no state is produced without a valid prior profile.

## Profile calculation
- Fixed row size: $50.
- Value area: 70% of total TPO count.
- Each 30-minute candle contributes one TPO to every price row intersected by its high-low range.
- POC ties resolve to the row nearest the midpoint of the profile's row-index span.
- Value area expands outward from POC by the larger adjacent TPO count; equal adjacent counts are handled in the Pine-compatible down-first order.
- Developing POC/VAH/VAL are recalculated using candles from the start of the current session through the current completed candle.

**Important limitation:** this is an OHLC-derived TPO approximation, not tick-by-tick or trade-by-trade Market Profile data. A candle wick crossing several rows contributes once to each row even though the candle does not reveal the time spent in each row.

## Price behaviour
- Discovery candidate: one completed close beyond prior VAH/VAL by the buffer.
- Discovery confirmed: two consecutive completed closes beyond the buffered boundary.
- `discovery_event_start=true` only on the candle where the internal confirmed side changes from unset to confirmed. Repeated `DISCOVERY_CONFIRMED_*` labels after a temporary re-entry candidate do not count as a new episode.
- Buffer: max($50, 0.5 x Wilder ATR(21)).
- A non-qualifying close resets the candidate sequence.
- Re-entry candidate: first completed close back inside frozen prior value after confirmed discovery.
- Re-entry confirmed: second consecutive completed close inside prior value.
- Price outside the unbuffered prior value, consecutive outside closes, and boundary retests are exposed separately from discovery state.

## Value development
Value development is a separate classification from price behaviour:
- Fully separated above/below.
- Broad migration up/down (POC, VAH and VAL all move at least one row in the same direction).
- POC migration up/down.
- Partial migration up/down.
- Overlapping value.

Price discovery is not treated as proof that new value has developed.

## Balance evidence
Balance evidence is a separate observation, not a replacement for price state:
- Track changes in the side of developing POC relative to frozen prior POC.
- Two or more side crossings in a rolling five-valid-candle window, while developing value overlaps prior value, sets `balance_evidence=true`.
- Boundary retest evidence is reported separately and is not silently converted into acceptance. It requires the preceding completed candle to have closed outside the same unbuffered prior-value boundary, the current candle to test back through that boundary, and the current close to remain outside. A gap invalidates the retest sequence.
- This balance heuristic is not yet historically validated as a reliable balance/rotation classifier. Synthetic tests now verify that two qualifying POC-side crossings can trigger it while price remains inside prior value, and that a missing candle resets the crossing window. It did not trigger in the historical sample.

## Historical data validation — refreshed 26 Nov 2026
Read-only checks against Supabase were repeated using the current archive:
- Raw Binance spot BTCUSDT 30m archive: **1,212 candles** (1 Nov through 26 Nov 2026), covering only **26 UTC dates**. The `session_ohlc_history` table contains session aggregates, not a substitute 30m raw-candle history. The current Supabase raw archive therefore cannot support a longer direct replay yet; broader validation requires continued collection or a separately identified public Binance historical fixture.
- 1–25 Nov: **25 complete sessions**, each with 48 consecutive candles.
- 26 Nov: **12 archived completed candles** at the time of the latest read-only query (00:00–05:30 UTC).
- No missing or irregular 30-minute intervals were found in the raw archive.
- Independent $50 / 70% profile calculation matched stored prior VAH/POC/VAL on **729 of 729 comparable snapshot rows**.
- Developing VAH/POC/VAL matched **682 of 682 comparable populated rows**.
- **47** AMT snapshot rows have null developing-value fields despite `data_status=VALID`.
- A refreshed read-only join of all 732 AMT snapshots found **one unmatched snapshot**: 26 Nov 06:00 UTC. The earlier 05:00 and 05:30 UTC snapshots are present in the raw OHLC archive. Inspection of collection timestamps explains the mismatch: the raw archive's latest fetch was at 06:27:51 UTC, before the 06:00 candle closed at 06:29:59.999 UTC; the AMT snapshot for that candle was collected at 06:31:26 UTC, after close. This is consistent with a collector scheduling race (raw fetch occurred about two minutes before candle close), not evidence of a missing completed candle. A later raw collection should pick it up; do not substitute AMT snapshot OHLC into the independent input.

These results establish profile arithmetic parity for the comparable rows only. They do not establish state-machine accuracy, predictive value, profitability, or tick-level Market Profile equivalence. The historical archive contained no missing 30-minute intervals, so gap-reset behaviour is covered by synthetic tests rather than a naturally occurring historical gap.

## Direct Python historical replay — offline fixture
A read-only fixture containing the archived raw Binance BTCUSDT spot 30m OHLC candles was added under `tests/fixtures/`. GitHub Actions executed the actual Python engine against all 1,212 records; no live database connection or database write was used in the test.

- The fixture covers 1–26 Nov 2026 and uses a fixed as-of time of 26 Nov 07:00 UTC so all 1,212 archived candles are completed.
- The Python engine produced **1,164 output bars** across 25 complete prior sessions plus the partial 26 Nov session; no natural data gaps were present.
- It identified **30 distinct discovery-confirmation episodes**: 6 upside and 24 downside. Episode counting uses `discovery_event_start`, not repeated per-candle confirmed-state labels.
- At confirmation, **7/30** episodes had fully separated developing value. Reviewing the remainder of each episode window, **12/30** achieved same-side value separation at some point (7 already separated at confirmation and 5 later); **18/30** did not achieve same-side separation before the next episode or session boundary. This strict measure requires the entire developing value area to clear the prior value area.
- A separate directional-migration audit includes `BROAD_MIGRATION`, `POC_MIGRATION`, `PARTIAL_MIGRATION`, and full `VALUE_SEPARATED` states in the discovery direction. By this broader descriptive measure, **27/30** episodes were directionally aligned at the confirmation candle, **27/30** within the first four completed candles, and **28/30** at least once before the episode window ended. **4/30** also showed some opposite-direction value migration during that window, while **2/30** never showed same-direction migration. These overlapping categories describe developing-profile movement, not a sustained acceptance test or a trade success rate.
- The distinction matters: lack of full value separation does not mean no value development. Most episodes showed some same-direction value migration, but the sample also contains mixed-direction migration. The four mixed-direction windows were 2 Nov 16:00 UTC (downside discovery with POC migration up only), 4 Nov 06:30 UTC (upside discovery initially with broad migration down, then POC migration up after 11 bars), 8 Nov 21:30 UTC (downside discovery with POC migration up only), and 16 Nov 12:30 UTC (downside discovery with POC migration down initially, then POC migration up later). The two episodes with no same-direction migration at any point were 2 Nov 16:00 UTC and 8 Nov 21:30 UTC. These are descriptive examples from this short sample, not a validated failure classification.
- There were 17 confirmed re-entry transitions and 53 boundary-retest-labelled candles, grouped into **35 contiguous retest-sequence starts**: 6 upside reclaim sequences and 29 downside rejection sequences. These are OHLC boundary tests, not proof of acceptance or a trade signal.
- The balance heuristic did not trigger. The maximum observed was **one developing-POC side crossing in any rolling five-candle window**; the required two crossings never occurred. This does not prove the market was never balanced. It shows the current heuristic was non-operative in this sample and should not be treated as a validated balance classifier.
- The historical episode ledger was emitted from the Python engine in CI and reviewed across all 30 episodes. Within each episode's remaining session window (or until the next confirmed episode), **17/30** reached confirmed re-entry: **5 within four candles**, another **12 after more than four candles**. The remaining **13/30** did not reach confirmed re-entry before that window ended. This is an outcome description, not a false-positive label; a session boundary or new episode ends the review window.
- The corrected episode-boundary audit also found **2 same-session opposite-direction discovery transitions** (2 Nov upside→downside and 4 Nov downside→upside). The prior report omitted these because it excluded the next episode's first candle from the preceding episode's observation window. The ledger now inspects that boundary candle explicitly.
- GitHub Actions Run #82 executed the expanded offline suite after the episode-boundary correction: **19 tests passed**, including the full raw-history replay, two opposite-transition assertions, synthetic balance/gap tests, historical retest-sequence count assertions, and the missing-first-session-candle test.

A follow-up persistence audit now separates the discovery-active interval from the post-re-entry interval. The active interval ends immediately before the candle that confirms re-entry; the post-re-entry interval begins on that confirmation candle. Across the 30 episodes:
- Same-direction value migration appeared while discovery remained active in **28/30** episodes. It was already present at confirmation in **27/30**, and present again within the next four completed candles in **27/30**.
- **3/30** showed opposite-direction migration while discovery remained active. One of those later migrated in the discovery direction; the other two never showed same-direction migration in their review window. No episode showed a same-direction-to-opposite-direction migration reversal while discovery remained active.
- After confirmed re-entry, **17** episodes still showed same-direction migration at some point, and **1** showed opposite-direction migration. Because the developing profile is cumulative, this post-re-entry movement must not be treated as continued price acceptance outside prior value.
- The five rapid re-entry episodes (confirmed re-entry within four bars) all had same-direction migration at confirmation, within the next four bars, and while discovery remained active (**5/5**); none showed opposite migration while active.
- The 12 slower re-entry episodes (re-entry after four bars) all showed same-direction migration at confirmation, in the next four bars, while discovery remained active, and after re-entry (**12/12**); none showed opposite migration while active.
- The 13 episodes with no confirmed re-entry before the next episode or session boundary showed same-direction migration at confirmation in **10/13**, in the next four bars in **10/13**, and at some point while discovery remained active in **11/13**. Three showed opposite migration while active; one of these later shifted back toward the discovery direction.
- These are small-sample descriptive comparisons, not a quality score or a basis for trading rules.

A focused candle-by-candle audit was added for the three episodes with opposite-direction value migration while discovery remained active, the two no-same-direction-alignment episodes (which overlap two of those three), and the 16 Nov case where opposite migration appeared only after re-entry. The trace includes completed-candle OHLC, price state, value state, and developing VAH/POC/VAL.

Key observations:
- **2 Nov downside discovery:** prior value 78,125 / 77,975 / 77,775; confirmation close 77,331.80; developing value 78,475 / 78,075 / 77,775. POC_MIGRATION_UP persisted through the 16-candle episode window, even as developing VAL fell to 77,325 and price traded as low as 76,772. This is a split response: POC remained marginally above prior POC while the lower value boundary migrated down.
- **4 Nov upside discovery:** prior value 77,625 / 77,125 / 76,625; confirmation close 78,007.91; developing value 77,525 / 77,025 / 76,375, initially a broad migration down. At 12:00 UTC the profile changed to 78,725 / 78,525 / 76,925 and broad migration up. The 12:00 candle ranged 78,001.77–78,500; its high touched the 78,500–78,550 TPO row, taking that row from seven TPOs to eight and changing the POC tie outcome. This demonstrates sensitivity to the OHLC row-touch and tie-break method, not proof of sudden full-range acceptance.
- **8 Nov downside discovery:** confirmation candle opened 75,713.36, reached 73,510 and closed 74,135.77, with about 2,325 BTC base volume. Developing value remained 76,075 / 75,875 / 75,725 at confirmation and POC stayed above prior POC, while VAL fell to 74,975 over the five-candle episode window. This was a late-session displacement with only five candles remaining; the OHLC TPO method counts a candle once per row touched, not by traded volume.
- **16 Nov downside discovery:** same-direction POC migration down persisted through confirmed re-entry at offset 4. Opposite POC_MIGRATION_UP appeared at offset 17, thirteen candles after re-entry. It is post-re-entry profile drift, not an active-discovery reversal.

Interpretation caveat: classify_value() prioritizes POC migration before partial VAH/VAL migration. Therefore POC_MIGRATION_UP means POC is at least one row higher; it does not mean VAH, POC, and VAL all moved up. The 2 Nov and 8 Nov episodes illustrate why the label must be read as a POC-specific statement. Broad migration requires all three levels to move in the same direction. This audit does not change the classifier or trading rules.

A full 30-episode audit compared the developing VAH, POC, and VAL independently with the frozen prior-session levels, always relative to each episode's discovery direction. Direction means strictly higher, lower, or unchanged; this is a level-position comparison, not a measure of volume acceptance.

At the discovery-confirmation candle:
- **24/30** had all three levels moving in the discovery direction.
- **2/30** had VAH and POC moving with discovery while VAL moved against it.
- **1/30** had VAH and POC moving with discovery while VAL was unchanged.
- **1/30** had VAH unchanged, POC against discovery, and VAL with discovery.
- **1/30** had all three levels against discovery.
- **1/30** had VAH and POC against discovery while VAL was unchanged.

Across the full discovery-active interval (from confirmation up to but excluding confirmed re-entry, or the episode-window boundary if no re-entry):
- Strict **POC-only alignment** (POC with discovery while neither VAH nor VAL is with discovery) occurred in **0/30** episodes.
- POC aligned with exactly one value-area edge at some point in **5/30** episodes.
- All three levels aligned with discovery at some point in **28/30** episodes.
- At least one value-area edge aligned while POC did not in **3/30** episodes.
These are overlapping episode-level flags, not mutually exclusive categories.

The five POC-plus-one-edge episodes were 4 Nov (15:00 UTC), 12 Nov (14:00 UTC), 16 Nov (12:30 UTC), 17 Nov (16:00 UTC), and 18 Nov (14:00 UTC). The three edge-without-POC episodes were 2 Nov (17:30 UTC), 4 Nov (08:00 UTC), and 8 Nov (21:30 UTC). The 4 Nov discovery appears in both sets at different candles, illustrating that the profile can change shape during one episode.

Examples show why the state label must be read literally:
- **2 Nov 17:30 UTC:** label `POC_MIGRATION_UP`; relative to downside discovery, VAH and POC were against while VAL was with (prior 78,125 / 77,975 / 77,775; developing 78,525 / 78,025 / 77,725).
- **4 Nov 08:00 UTC:** label `POC_MIGRATION_DOWN`; relative to upside discovery, VAH was with while POC and VAL were against (prior 77,625 / 77,125 / 76,625; developing 77,675 / 77,075 / 76,075).
- **8 Nov 21:30 UTC:** label `POC_MIGRATION_UP`; relative to downside discovery, VAH was unchanged, POC was against, and VAL was with (prior 76,075 / 75,825 / 75,775; developing 76,075 / 75,875 / 75,725).

This confirms that the classifier's POC-first label can point in a direction that does not describe the movement of the full value area, and can even be opposite the discovery direction. But the audit found no episode where POC alone aligned while both edges did not. The recurring issue is **split POC/edge migration**, not a pure POC-only profile shift. Do not infer broad value migration from a POC label.

A follow-up timing audit measured whether all three developing levels (VAH, POC, VAL) remained displaced in the discovery direction after the first four completed candles following discovery confirmation. Offset 0 is the confirmation candle; offsets 1–4 are the first four subsequent candles; offset 5 onward is after those four. The active interval ends immediately before confirmed price re-entry, consistent with the persistence audit above. This measures level-position alignment against frozen prior value, not trade-volume acceptance or a trading outcome.

Across the 30 episodes:
- **24/30** had all three levels aligned at confirmation.
- **27/30** had all three aligned at least once within the first four subsequent candles.
- **21/30** had all three aligned at least once after the first four subsequent candles, while discovery remained active.
- **28/30** had full three-level alignment at some point during the active interval.
- Across the 440 eligible active candles at offset 5 or later, **385/440 (87.5%)** had all three levels aligned with discovery. Across the full 579 active candles, the figure was **502/579 (86.7%)**.

By re-entry cohort:
- **Rapid re-entry (5 episodes):** no episode had an active candle after offset 4; these episodes re-entered too early for this persistence test.
- **Slower re-entry (12 episodes):** 11/12 had full alignment after the first four subsequent candles; all 149/149 eligible active candles were aligned.
- **No confirmed re-entry in the review window (13 episodes):** 10/13 had full alignment after the first four subsequent candles; 236/291 eligible active candles (81.1%) were aligned.

Interpretation: broad three-level migration is often present early, but it is **not merely an early-session profile response** in this sample. It remained aligned well beyond the first four subsequent candles in 21 episodes, and most eligible active candles after that point retained the alignment. Persistence was strongest in the slower-re-entry cohort. The rapid-re-entry cohort cannot be judged on later persistence because its active interval ended before the measurement window began.

### Price behaviour during broad migration

A separate descriptive audit compared each completed candle's close-to-close movement with the discovery direction, splitting active candles into (a) broad-alignment states (all three levels displaced in the discovery direction: `BROAD_MIGRATION_*` or `VALUE_SEPARATED_*`) and (b) the other active value states. For each episode, signed close changes were summed separately within each subset. Positive means price moved in the discovery direction; negative means it moved against it. This is a filtered set of candle-to-candle steps, not the full continuous price path.

Results:
- Broad-alignment subset: **502 candles**; 243 moved with discovery, 259 against. Summed signed close change across these selected steps was **+1,336.47**.
- Other active value states: **77 candles**; 43 moved with discovery, 34 against. Summed signed close change was **+4,581.80**.
- At the episode level, broad-alignment steps had a positive net in **11/28** episodes and a negative net in **17/28**; median episode net was **−301.85**.
- The other-state subset existed in only **7 episodes**; 6 had a positive net and 1 negative, median **+703.24**. This small, selected comparison group is not a fair standalone control.

Interpretation: persistent broad value alignment did **not** reliably coincide with continued close-to-close price progress in the discovery direction. Despite alignment on 502 active candles, its signed candle-step direction was nearly balanced (243 versus 259), and the episode-level net was negative in more episodes than positive. The aggregate positive sum is sensitive to a few large moves and should not override the episode-level distribution. This supports a careful distinction: the profile can remain broadly displaced while price pauses, retraces, or rotates. Broad migration describes the location of the developing distribution relative to prior value; it is not evidence by itself that price is continuing in that direction.

Important limitations:
- The selected broad-alignment candles are not necessarily consecutive; intervening candles in other value states are excluded from that subset.
- Close-to-close signed steps are not trade returns, do not include execution costs, and do not measure the full excursion or maximum adverse movement.
- The 30 episodes are a small historical sample from the available archive. No predictive or causal claim is justified.

This is descriptive evidence from 30 historical episodes, not a predictive result, profitability test, or reason to alter AMT state logic. The timing and cohort counts are asserted in the historical replay test. GitHub Actions Run #128: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775650880 (**19 tests passed**). Session OHLC Writer Run #101: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775650744 and Binance OHLC Archive Run #93: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775650889 also passed.

The direction-signature counts, active-interval shape counts, and the exact episode lists are asserted in the historical replay test. GitHub Actions Run #123: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775143458 (**19 tests passed**). Session OHLC Writer Run #98: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775143544 and Binance OHLC Archive Run #90: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45775143543 also passed.

The focused trace ran in GitHub Actions Run #108: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45774029962 (**19 tests passed**).

The persistence audit, separate rapid/slow/no-re-entry cohort assertions, and full historical replay ran in GitHub Actions Run #102: https://github.com/shashibangera-spec/btc-dashboard/actions/runs/45773110378 (**19 tests passed**).

These are descriptive historical state counts only. They do not establish false-confirmation rates, predictive value, or profitability. Automated assertions spot-check three rapid re-entry sequences (4 Nov, 17 Nov, 22 Nov) and two sustained discovery sequences (21 Nov downside, 26 Nov upside); the CI ledger provides the full 30-episode review trail.

## State-machine validation still required
- Manually review the 30 discovery episodes against completed candles, especially quick re-entry and opposite-side transitions.
- Review candidate cancellation, opposite-side transitions, re-entry, and session rollover manually against completed candles.
- Review whether the balance heuristic's POC-crossing condition is a useful description of rotation on a larger historical sample; synthetic mechanics tests alone do not validate the market interpretation.
- Measure false confirmations and cases where price discovery is not accompanied by value development.
- Resolve raw OHLC archive lag before claiming current-session replay completeness.

## Safety and release
- Read-only Supabase credential required: `SUPABASE_READ_ONLY_KEY`, or an `SUPABASE_ANON_KEY` with SELECT permission.
- No service-role credential is required or used by this module.
- This branch is research-only. Do not connect it to the production dashboard, collector, alerts, Playbook or trading workflow until state-machine validation and explicit release approval.



## Full price-path and price-acceptance audit (30 discovery episodes)

A second, independent replay audit follows the completed 30-minute OHLC path from each discovery-confirmation candle. The measurement window ends immediately before the first two-close confirmed re-entry, or at the next discovery event/session boundary if no confirmed re-entry occurs first. This prevents post-re-entry movement or a later discovery episode from being attributed to the original episode. The confirmation candle is included as offset zero. Directional excursions are measured from its close; wick excursions use the full candle highs/lows. “Outside prior value” means a completed close beyond frozen prior VAH/VAL without the ATR buffer; “beyond buffer” additionally clears the candle-specific discovery buffer.

The audit independently reconstructs the raw fixture and confirms **1,212 completed raw candles**, **1,164 replay snapshots**, and **30 discovery event starts** (6 upside, 24 downside). Across the 30 active windows:
- **17/30** reached confirmed two-close re-entry before the review window ended; **5/30** did so within four candles. The other 13 did not re-enter before a session boundary or a new episode.
- The active windows contain **579 candles** in total (range 2–47 candles per episode).
- **19/30** episodes ended with price net against the discovery direction and **11/30** ended net with it. The median episode-end signed move from the confirmation close was approximately **−$310.66**; the mean was approximately **+$127.90**, showing that a small number of large favourable moves materially affect the aggregate.
- Median maximum favourable close excursion was about **$548**, versus median maximum adverse close excursion of about **$319**. These are path excursions, not realised or executable trade returns. Wick-based excursions were larger: median favourable wick excursion about **$722**, median adverse wick excursion about **$490**.
- **559/579 (96.5%)** active-window closes were beyond the frozen prior value boundary in the discovery direction; **509/579 (87.9%)** also cleared the candle-specific discovery buffer. **12/30** episodes had every active-window close outside prior value. This is expected in part because the confirmation condition itself requires two consecutive buffered closes; it must not be read as independent validation of acceptance.
- Across successive close-to-close steps inside the active windows, **269** moved with discovery and **278** moved against it (remaining steps unchanged). Directional progress was therefore not a one-way path, even where price stayed outside prior value.

Interpretation: price can remain outside prior value while making little net progress, retracing substantially, or ultimately returning to value. Conversely, several episodes produced large favourable excursions and remained outside value to the session boundary. “Outside value”, “discovery confirmed”, “value migration”, and “continued directional progress” are different observations. None alone establishes a trade edge. The audit does not change the engine, state machine, dashboard, or trading rules.

Methodological caution: this is an OHLC-based replay using the branch's $50 row / 70% TPO approximation and the current two-close state definition. Intrabar ordering is unknowable from 30-minute OHLC, so wick excursions are maximum ranges, not a path sequence. A close returning inside prior value is not itself confirmed re-entry; the engine requires two consecutive closes. The 30 episodes are a small, selected sample from roughly 25 sessions, and the discovery events are not independent observations. The findings are descriptive and do not establish predictive value, causal acceptance, or profitability. Tick/trade-level TPO and a longer untouched sample would be needed to validate actual distribution acceptance and robustness.



## TPO parameter sensitivity audit (12 configurations)

The historical replay was repeated over the same 1,212 completed raw Binance spot 30-minute candles, changing only TPO row size and value-area percentage. All other mechanics were held constant, including the 05:30 IST session, ATR(21), the $50 minimum discovery buffer, 0.5×ATR buffer multiplier, two consecutive completed closes, and the same gap/session handling. The baseline is $50 rows / 70% value area. Event overlap is measured by exact match of session date, confirmation candle timestamp, and discovery direction against the 30 baseline event starts.

| TPO row | Value area | Discovery starts | Up / down | Baseline event matches |
|---:|---:|---:|---:|---:|
| $25 | 65% | 28 | 5 / 23 | 17/30 (56.7%) |
| $25 | 70% | 26 | 5 / 21 | 20/30 (66.7%) |
| $25 | 75% | 25 | 5 / 20 | 15/30 (50.0%) |
| $50 | 65% | 29 | 6 / 23 | 22/30 (73.3%) |
| **$50** | **70%** | **30** | **6 / 24** | **30/30 (100%)** |
| $50 | 75% | 27 | 6 / 21 | 20/30 (66.7%) |
| $75 | 65% | 28 | 5 / 23 | 21/30 (70.0%) |
| $75 | 70% | 27 | 4 / 23 | 22/30 (73.3%) |
| $75 | 75% | 26 | 4 / 22 | 17/30 (56.7%) |
| $100 | 65% | 32 | 7 / 25 | 18/30 (60.0%) |
| $100 | 70% | 29 | 7 / 22 | 19/30 (63.3%) |
| $100 | 75% | 30 | 7 / 23 | 18/30 (60.0%) |

Across the 12 configurations, discovery counts range from **25 to 32**, and exact event overlap with the baseline ranges from **50.0% to 73.3%** outside the baseline configuration. The baseline's 100% overlap is expected because it is being compared with itself. The number of broad value-separation labels at the confirmation candle also changes: **4 to 10** across the alternatives, versus **7** at baseline. Confirmed re-entry candle counts range from **12 to 17**.

Interpretation: the broad conceptual distinction remains valid—price-state confirmation and developing-value migration are separate classifications—but the identity and count of individual discovery episodes are materially sensitive to the TPO row and value-area settings. Consequently, the earlier 30-episode price-path statistics are conditional on the $50 / 70% construction, not parameter-invariant market facts. We should not promote their exact proportions, episode timestamps, or re-entry frequencies as robust until a longer sample and independent tick/trade-level profile comparison are available.

This is a sensitivity check, not a search for the “best” row size. No alternative configuration is recommended as a replacement. The Pine production reference and the dashboard's locked settings are unchanged.
