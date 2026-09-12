# Known limitations

Written before anyone else could write it for me. A tool that only documents its successes is telling you what it wants you to believe.

## Device leasing does not span processes

`DevicePool` tracks leases in memory. Two `maestro-plus` servers on one machine, or one server plus a colleague running flows by hand on the same emulator, will not see each other's work.

A lock file would close this hole and open a worse one: a crashed run leaves a stale lock, and the next person spends twenty minutes on a device that is free but marked busy. An honest boundary beats a clever failure mode. If you need cross-process leasing, that belongs behind a real scheduler.

## The fallback path is slow

When the official MCP server is unavailable, screen reading goes through `adb shell uiautomator dump`, which costs roughly a second per call on a mid-range device. A diagnosis pass makes three or four such calls, so the degraded path is visibly slower than the primary one.

This is a recovery mechanism, not a default. It exists so a broken MCP server is an inconvenience rather than a stopped shift.

## No accessibility tree means no real diagnosis

`debug_failure` distinguishes an element that is missing from an element that is present but invisible or disabled. It cannot tell you *why* an element is absent when the app never rendered it, and it cannot inspect anything the accessibility tree does not expose:

- WebView content, depending on the app's accessibility configuration
- Flutter and game canvases rendered without semantics nodes
- Custom-drawn views that report a single opaque node

When the tree is empty, attribution drops to `unknown` with low confidence. That is the honest answer, not a shortcoming of the heuristic.

## Attribution is a heuristic and thrice wrong

The rules are ordered from unambiguous to ambiguous. A crash or an ANR is answerable. A locator that does not resolve is not: one failed run genuinely cannot separate a stale test from a screen that never arrived.

Expect the `unknown` verdict on real-world failures, and read the findings rather than the label. The confidence field exists because reporting low confidence as high would send a bug report to the wrong team, which is worse than reporting nothing.

## Maestro CLI output parsing is version-sensitive

The exit code is the only signal treated as authoritative, and it is stable. Everything else degrades quietly:

- JUnit reports are located by scanning Maestro's own output directory, because `--format junit` is not present in every release.
- Failing-step extraction falls back to a shallow scan of console output, which will break when Maestro changes its formatting.

Neither one can fail a run. Both can make a diagnosis less precise.

## Android only

The device pool, fallback path, and evidence collection are built on `adb`. iOS needs `simctl` and a second backend. The interfaces are written to accommodate one, but the work is not done and pretending otherwise would waste your time.

## Not a test management system

No history, no dashboards, no scheduling, no flake trends over time. `run_parallel` returns a speedup for one batch and forgets it. Anything that needs to remember results across runs belongs in the CI system you already have, not here.

## The evaluation numbers are not filled in yet

The README carries an evaluation table with a `not yet measured` status on every row. That is accurate. Measuring parallel speedup and attribution accuracy requires a real suite and a hand-labelled failure set, and inventing plausible numbers would defeat the entire purpose of having the table.

If you are evaluating this project and the empty table bothers you, that instinct is correct — and it is the first thing on the roadmap for a reason.
