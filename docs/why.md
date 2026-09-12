# Why this project exists

A portfolio project should be able to answer "why not just use the thing that already exists?" in one page. This is that page.

Every claim below is checkable against Maestro's own documentation and repository. None of it is a guess about what upstream might do later; it is what upstream does now.

## What the official MCP server gives you

Maestro ships an MCP server inside its CLI. It exposes five tools:

| Tool | What it does |
| --- | --- |
| `list_devices` | Lists local emulators, simulators, and browser instances |
| `inspect_screen` | Reads the current view hierarchy as JSON |
| `take_screenshot` | Captures the screen |
| `run` | Executes a flow, inline YAML or a file |
| `cheat_sheet` | Returns the command reference |

That is a good API for letting an agent see a device and touch it. It is deliberately not an API for deciding whether a run was correct.

## Gap 1 — one device

`run` targets a single device. There is no pool, no lease, and no scheduling.

This is not an oversight. Parallel execution across devices is a paid feature of Maestro Cloud, priced per concurrent device. Local parallelism is therefore something upstream has a commercial reason to leave out, which is exactly why it is safe to build.

Parallelism also needs an owner for each device. Two runs against one emulator produce a failure that looks like an application defect. Maestro's own tool has no concept of a device being busy because it never has two runs to reconcile.

**What this project adds:** `list_device_pool`, `run_parallel`.

## Gap 2 — no assertions

`run` reports pass or fail for the flow it executed. There is no `assert`, and there is nothing that evaluates a condition against the resulting screen.

So an agent that wants to verify something does this: run the flow, take a screenshot, read the hierarchy, reason about what it sees, decide. Four round trips per verification, and the conclusion exists only inside that conversation — unverifiable, unrepeatable, and gone when the context window rolls over.

**What this project adds:** `run_and_assert`, `assert_visual`.

## Gap 3 — no diagnosis

When a flow fails, you get a failure. You do not get the failure step, the screen at the moment it happened, the relevant slice of logcat, the focused window, or an opinion about who is at fault.

That last part is the expensive one. "Is this the app or my test?" is the first question any QA engineer asks, and answering it currently means a person attaching to a device and looking.

**What this project adds:** `debug_failure`.

## Gap 4 — recording is closed source

Maestro Studio records interactions and generates YAML. Studio is free to use but its source is not published — the open-source line runs through the CLI and the MCP server, and recording sits on the closed side of it.

The valuable half of recording is not capturing taps. It is generating the assertions at the end, which is the part of a flow people write badly, skip, or let rot.

**What this project adds:** `explore_and_record`, which takes the steps an agent already performed, emits a flow, derives assertions from the screen the steps left behind, and then runs the result to prove it replays.

## Gap 5 — no self-check

Nothing tells you why the toolchain is not working. A missing binary, an unauthorized device, and a wedged emulator all present as a tool that hangs or returns something strange.

**What this project adds:** `health_check`, which reports per-tool availability and a degradation map.

## What this project deliberately does not do

It does not reimplement any of the five official tools.

That is a design rule, not a courtesy. The failure mode for a project like this is upstream shipping the same feature six months later and leaving you maintaining a worse copy. Everything here is an orchestration layer sitting above the official tools, so upstream growth is additive rather than fatal.

The one place this project goes direct is the Maestro CLI itself, which is what the official MCP server wraps internally. Going straight to the CLI saves a process and a protocol hop, and it means `maestro-plus` keeps working when the official MCP server is unavailable — which is the second design rule: always degrade, never fail.

## How to check these claims

Every gap above is verifiable in an afternoon:

1. Run the official MCP server and call `list_tools`. The five tools are all it has.
2. Search Maestro's issue tracker for device pooling and for assertion primitives.
3. Read the Maestro pricing page and find which capability is listed per concurrent device.

If a claim here turns out to be wrong, that is a bug in the documentation and worth an issue.
