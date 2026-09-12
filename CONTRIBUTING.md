# Contributing

Thanks for considering a contribution. The rules below are short because the project's design rules are enforced by tests, not by reviewer memory — but the ones that aren't testable still matter.

## Setup

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

No device is needed. Tests that would require one (everything shelling out to adb) are deliberately not part of the suite; what runs in CI is the logic that decides things — flow generation, evidence de-duplication, attribution ordering.

## Two design rules

1. **Never reimplement an official Maestro tool.** If the upstream MCP server exposes it, wrap it. `test_official_maestro_tools_are_not_duplicated` fails the build if a new tool collides with one of the five official ones. Extensions to upstream capabilities belong in orchestration above them.
2. **Always degrade, never fail.** If the official MCP server is unavailable, the tool should fall back to `adb` plus the Maestro CLI and still produce a useful result. A tool that says "the official server is down" has told the user nothing they can act on.

## Other expectations

- **Honest uncertainty.** Any heuristic (attribution, matching, parsing of upstream output) must report its confidence, and `unknown` is an acceptable answer. A diagnosis that rounds low confidence up to high sends bug reports to the wrong team.
- **Tests carry the reasoning.** When you fix a bug, the regression test's name should state the invariant, and its failure message should explain why the invariant matters.
- **Long fixtures stay long.** Test fixtures intentionally carry wide XML and logcat literals so generated output can be compared against real device output. `tests/**` is exempt from line-length for this reason; don't re-wrap them.
- **Commit messages explain why.** The what is in the diff. What future maintainers need is the decision behind it.

## Reporting bugs

Run `health_check` first and attach its output. Roughly half of reported failures are a missing binary or an unauthorized device, and the report answers that in one call. Then include: Maestro version (`maestro --version`), `adb devices -l` output, and the smallest flow that reproduces the failure.

If the bug is in `debug_failure`'s attribution, say what the real root cause was — calibration data is the scarcest input this project can receive.
