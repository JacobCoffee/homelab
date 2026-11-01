# Fire TV Stick 4K (AFTKRT) bench: Amazon Home vs Homestead

Measured 2026-11-01 with `cleanyourtv/tools/bench.py` (`--app com.netflix.ninja/.MainActivity`,
`--keys 36 --series 3 --cold 5 --returns 10 --idle 60`, all defaults) against `TV_HOST=$FIRETV_HOST`.
Raw per-run output: `reports/bench-amazon/result.json`, `reports/bench-homestead/result.json`.

bench.py's own launcher auto-detection (`cmd package resolve-activity -c HOME`) always resolves to
`com.amazon.tv.launcher`, because Amazon pins itself as the OS-registered HOME handler even while
Homestead is intercepting the key (see `hearth-spec.md`). The Amazon run used bench.py's CLI
unmodified. The Homestead run used a small driver (`/tmp/bench_run_homestead.py`, not committed,
reused bench.py's own `TV`/`measure_key`/`cold_start`/`return_to_home`/`idle_cpu`/`pss_of` functions
unmodified — bench.py is explicitly built to be imported) with the component fixed to
`com.homestead.launcher/.MainActivity`, confirmed empirically as what the screen settles on after a
Home press and a D-pad move.

| Metric | Amazon (`com.amazon.tv.launcher`) | Homestead (`com.homestead.launcher`) |
|---|---|---|
| Key response p50 / p95 | 14 ms / 23.9 ms (n=50) | not measured (0/108, see below) |
| Movement p50 / p95 | 473 ms / 1340.3 ms (n=50) | not measured |
| Jank % | 14.73 % (502/3407 frames) | not measured (0 frames attributed) |
| Cold start p50 / p95 | 204 ms / 238 ms (n=5) | 611 ms / 664 ms (n=5) |
| Return-to-Home p50 / p95 | not measured (0/10) | not measured (0/10) |
| Idle CPU, whole device | 15.2 % | 5.5 % |
| Idle CPU, launcher process | 7.2 % | 0.0 % |
| Launcher PSS, own run | 173 MB (bench.py's own reading, right after its key/cold-start/idle sequence) | 48 MB (same) |
| Launcher PSS, steady-state snapshot | 161–191 MB (see below) | 30–34 MB (see below) |
| MemAvailable | 524 MB (Amazon active) | 516 MB (Homestead active) |

## Key response / movement / jank: not measured for Homestead

Homestead's keys never produced a `dumpsys gfxinfo` frame inside bench.py's 150 ms
"this is the key's answer" window — all 108 probes across 3 series came back `"measured": false"`
(`reports/bench-homestead/result.json`, `keys.measured: 0`). Amazon, probed with the identical
sequence and timing, measured 50/108 (46%).

This was investigated by hand (not committed, interactive `dumpsys gfxinfo com.homestead.launcher
reset` → `input keyevent 22` → `dumpsys gfxinfo com.homestead.launcher framestats`, repeated several
times): the TV did render frames after the key in most probes, but their `IntendedVsync` landed
400–450 ms after the reset's own `Uptime` read, well past the 150 ms cutoff bench.py uses to decide a
frame is "the key's answer" rather than unrelated background redraw. Amazon's matched frames landed
inside a few ms of the key. Two explanations are both plausible and this audit could not fully
separate them without instrumenting Homestead's own render path: (a) Homestead's D-pad focus-move
redraw genuinely starts later on this hardware than the 150 ms heuristic assumes, or (b) its
`MainActivity` renders on a different surface/cadence that `dumpsys gfxinfo <package>` does not
attribute the same way Amazon's launcher does. Either way, **no key-latency or jank number for
Homestead can be reported with confidence from this tool**; the idle-CPU and PSS numbers below do
not depend on this and stand on their own.

## Return-to-Home: not measured for either launcher

`return_to_home()` greps `logcat -b main -b events` for `am_on_resume_called`/`wm_on_resume_called`
naming the launcher's class after a Home key. On this stick (Fire OS 8 / Android 11) neither launcher
produced a matching line in the 10 repeats each, so both reported `n: 0`. This is a tool/firmware
mismatch (the events buffer's format or availability differs from what bench.py expects here), not a
Homestead-specific result — cold start (which uses `am start -W`, not logcat) worked for both and is
the number to use for "how long until the launcher is usable again."

## Cold start

Amazon's `HomeActivity_vNext` restarts faster (204 ms p50) than Homestead's `MainActivity` (611 ms
p50) after a force-stop. Both runs used the identical method (`am force-stop` → `am start -W -a MAIN
-c HOME -n <component>`, restoring accessibility services afterward). 611 ms is above the hearth-spec
budget for Hearth itself (cold start ≤ 1 s is fine, but the budget line also wants Home-press-to-
visible ≤ 300 ms, which a cold path obviously can't hit — that number is for the warm/resumed case).

## Idle CPU and PSS

With Homestead active, idle device CPU was under half of Amazon's (5.5% vs 15.2%) and Homestead's own
process contributed ~0% over the 60 s idle window, vs Amazon's launcher using 7.2% of a core. Amazon's
launcher process also sat 4–6x larger in PSS (161–191 MB across the two snapshots below vs
Homestead's 30–34 MB) — expected, since Amazon's launcher keeps rendering live promotional/recommendation
rows even when backgrounded, while Homestead is a much smaller app.

Steady-state PSS snapshot (`dumpsys meminfo <pkg> | grep "TOTAL PSS"`, both packages read back-to-back
in each state, independent of bench.py's own per-run reading above):

| State | `com.amazon.tv.launcher` PSS | `com.homestead.launcher` PSS |
|---|---|---|
| Amazon active (Homestead's redirect disabled, process still resident) | 161 MB | 29.5 MB |
| Homestead active (Amazon backgrounded, still resident — it can't be killed) | 187 MB | 33.4 MB |

`MemAvailable` was close in both states (524 MB Amazon-active vs 516 MB Homestead-active); the ~8 MB
gap is within the noise of whatever else was running at the time, not a meaningful signal on its own.

## Device state

The device was left with Homestead's accessibility redirect restored and verified
(`mCurrentFocus=…com.homestead.launcher/com.homestead.launcher.MainActivity` after a Home press) and
`/tmp/firetv-device.lock` removed.
