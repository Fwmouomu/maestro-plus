<div align="center">

# maestro-plus

**Multi-device pools, composite assertions, and automatic failure diagnosis
for AI agents driving mobile UI tests.**

Built on top of Maestro's official MCP server — an orchestration layer above it,
never a replacement. When upstream grows, this project grows with it instead of dying.

[![CI](https://github.com/Fwmouomu/maestro-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Fwmouomu/maestro-plus/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Fwmouomu/maestro-plus)](https://github.com/Fwmouomu/maestro-plus/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-3B6D11)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-534AB7)](https://modelcontextprotocol.io)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-854F0B)](https://docs.astral.sh/ruff/)

[Install](#install) · [Tools](#tools) · [Why it exists](docs/why.md) · [Known limitations](docs/limitations.md) · [中文说明](README.zh-CN.md)

</div>

---

Maestro's MCP server gives an agent eyes and hands: it can read the screen, tap, and run a flow. It deliberately stops there — one device, atomic operations, and no opinion about whether the result was correct.

`maestro-plus` fills in what the official server leaves out.

## Capability matrix

| Capability | Official MCP | maestro-plus |
| --- | --- | --- |
| List connected devices | Yes | Yes |
| Device pool with health and occupancy state | No | Yes — `list_device_pool` |
| Run a single flow | Yes | Yes |
| Run N flows across M devices in parallel | No | Yes — `run_parallel` |
| Read the current screen | Yes | Yes |
| Assert on the outcome (element / text / visual) | No | Yes — `run_and_assert`, `assert_visual` |
| Decide *why* a run failed | No | Yes — `debug_failure` |
| Turn an exploration into a replayable flow | No (Studio is closed-source) | Yes — `explore_and_record` |
| Self-check the local toolchain | No | Yes — `health_check` |
| Keep working when the official MCP is unavailable | No | Yes — adb fallback |

The two tools that matter most are `run_parallel` and `debug_failure`. Parallel execution across devices is Maestro Cloud's revenue line, so it will never appear in the open-source CLI. Failure diagnosis is the work nobody wants to automate, which is exactly why it is worth automating.

## Install

### MCP host (Claude Code, Codex, Cursor, anything speaking MCP)

```json
{
  "mcpServers": {
    "maestro-plus": {
      "command": "npx",
      "args": ["-y", "maestro-plus"]
    }
  }
}
```

The npm package is a launcher only. It locates a Python runtime, starts the real server, and hands over stdin/stdout. There is no logic in it beyond that.

### Python-native

```bash
uvx maestro-plus
```

Or pin it into a project:

```bash
uv add maestro-plus
```

Requires Python 3.10+.

## Prerequisites

`maestro-plus` shells out to tools you already use. It does not bundle them.

| Tool | Required for | Install |
| --- | --- | --- |
| Maestro CLI | every flow-running tool | `curl -Ls "https://get.maestro.mobile.dev" \| bash` |
| adb | device discovery, fallback path, log collection | Android platform-tools |
| ffmpeg | only `explore_and_record` video capture | your package manager |

Run `health_check` first. It reports which of these are missing and which tools degrade without them.

## Tools

### `list_device_pool`
Returns every connected emulator and physical device with its serial, model, Android version, and whether it is currently leased by another run. Agent-facing tools use this to decide where work can go.

### `run_parallel`
Takes a set of flows and a set of devices, leases devices from the pool, runs the work, and returns one aggregated result. Results are per-flow and per-device, so a flake on one device does not mask a real failure on another.

### `run_and_assert`
Runs a flow, then evaluates a list of assertions against the resulting screen state in the same call. This replaces the loop agents get stuck in today: run, screenshot, read the hierarchy, describe what is visible, decide, repeat.

### `assert_visual`
Compares a screen region against a baseline image with a configurable tolerance, returning a diff percentage and the diff artifact path. Baselines can be regenerated on demand, which is what keeps this from becoming the flaky part of your suite.

### `debug_failure`
The flagship tool. Given a failed run, it collects and correlates the failure step, the screenshots immediately before and after, the relevant slice of device logs, the view hierarchy at the failure point, and emits a structured diagnosis that separates *application defect* from *test defect* — the question every QA engineer actually asks first.

### `explore_and_record`
Takes the steps an agent already performed, emits a Maestro flow, reads the resulting screen to generate the assertions — the part of a flow people get wrong — and then runs the finished file to prove it replays.

Not a drop-in replacement for Maestro Studio's recorder: this does not watch the screen and infer what you did. What it adds over Studio, which is closed source, is deriving the assertions. Recording taps is the easy half; assertions are where recorded flows rot.

### `health_check`
Verifies Maestro CLI version, adb availability, device reachability, and whether the official MCP server is responsive. Returns a degradation map: which tools work now, which are impaired, and what to install.

## Architecture

```
src/maestro_plus/
  server.py            # MCPServer instance and the complete tool registration
  pool.py              # device leasing: one owner per device at a time
  tools/
    devices.py         # health_check, list_device_pool
    parallel.py        # run_parallel
    assertions.py      # run_and_assert, assert_visual
    diagnostics.py     # debug_failure
    record.py          # explore_and_record
  backends/
    maestro_cli.py     # the primary path: wraps the Maestro CLI
    adb.py             # discovery, evidence collection, degradation path
  evidence.py          # screenshot de-duplication, log triage, hierarchy queries
  report.py            # diagnosis bundle and HTML rendering
```

Two rules keep the design honest:

1. **Never reimplement an official tool.** If upstream exposes it, wrap it. There is a test that fails the build if anyone forgets.
2. **Always degrade, never fail.** If the official MCP is down, fall back to `adb` + the Maestro CLI rather than returning an error.

## Evaluation

This section exists because a portfolio repository should show its work, including the parts that do not look good. Every row is `not yet measured`, and that is accurate rather than an omission — inventing plausible numbers would defeat the point of having the table.

| Claim | How it is measured | Status |
| --- | --- | --- |
| Parallel execution reduces wall time | Total run time for N flows on M devices vs. sequential | Not yet measured |
| `debug_failure` correctly attributes the defect | Hand-labelled set of failures with a known root cause | Not yet measured |
| Screenshot de-duplication compresses evidence | Artifact count before and after de-duplication | Not yet measured |

The first and third rows are mechanical. `scripts/measure.py` produces them against your own suite and prints finished markdown:

```bash
python scripts/measure.py --flows flows/ --devices emulator-5554,emulator-5556
python scripts/measure.py --flows flows/ --devices emulator-5554 --artifacts ~/.maestro-plus/artifacts
```

The second row is not automatable on purpose. Judging whether a diagnosis was right needs someone who knows the real root cause, so the script prints the labelling template rather than an accuracy figure.

Until the numbers exist, treat the claims above as design intent, not results.

## Known limitations

- **Android only today.** The device pool and fallback path are built on `adb`. iOS needs `simctl` and a second backend.
- **The fallback path is slower.** `uiautomator dump` costs roughly a second per read, so it is a recovery mechanism, not a default.
- **No accessibility tree, no diagnosis.** `debug_failure` can tell you an element was not found. It cannot tell you the app never rendered it if the app exposes nothing to the tree.
- **Not a test management system.** No history storage, no dashboards, no scheduling. It is a tool layer for agents and CI.
- **Depends on Maestro CLI output stability.** Where the CLI only offers human-readable output, parsing is fragile and version-sensitive.

## Roadmap

- [ ] iOS backend over `simctl`
- [ ] Fill in the evaluation table with numbers from a real suite and a hand-labelled failure set
- [ ] Progress reporting for long flows, so a four-minute run is not a black box
- [ ] Optional HTTP transport, so a team can share one device pool instead of one pool per machine

## License

MIT. See [LICENSE](LICENSE).
