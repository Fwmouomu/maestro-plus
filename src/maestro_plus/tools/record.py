"""Turn a described exploration into a replayable flow.

Maestro Studio records interactions into YAML and is closed-source. This is not a
drop-in replacement: it does not watch the screen and infer what you did. It takes
the steps an agent already performed, emits a flow, and — this is the useful part
— reads the resulting screen to generate the assertions, which are the part of a
flow people reliably get wrong.

The output is validated by running it. A generated flow that has been shown to
work is worth more than one that merely looks right, and that difference is what
separates a recording feature from a suggestion.

YAML is emitted through ``json.dumps`` rather than a template. JSON is a subset of
YAML, so every scalar and flow-style mapping this module produces is legal YAML
with correct escaping — and hand-rolled quoting is exactly where a generator
quietly breaks on a colon inside a button label.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .. import evidence
from ..backends import adb, maestro_cli
from ..errors import MaestroPlusError, ToolError
from ..pool import POOL

logger = logging.getLogger(__name__)

INDENT = "  "
MAX_GENERATED_ASSERTIONS = 8
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class RecordedStep(BaseModel):
    """One interaction performed during exploration, in replay order."""

    action: Literal[
        "launch",
        "tap",
        "input",
        "assert_visible",
        "assert_not_visible",
        "scroll",
        "scroll_until",
        "back",
        "swipe",
        "wait",
    ] = Field(description="What to do. `launch` restarts the app under test.")
    text: str | None = Field(
        default=None, description="Visible label to target or assert on."
    )
    resource_id: str | None = Field(default=None, description="resource-id to target.")
    value: str | None = Field(default=None, description="Text to type, for the `input` action.")
    direction: Literal["UP", "DOWN", "LEFT", "RIGHT"] | None = Field(
        default=None, description="Direction for `scroll`, `scroll_until`, and `swipe`."
    )
    timeout_ms: int | None = Field(
        default=None, ge=100, description="Timeout in milliseconds for `wait` and `scroll_until`."
    )


def _yaml_scalar(value: object) -> str:
    """Serialise any value this module emits as legal YAML."""
    return json.dumps(value, ensure_ascii=False)


def _selector(step: RecordedStep) -> object | None:
    """Build a Maestro selector from whichever locators the step carries.

    Both locators together when both are present: resource-id survives a copy
    change and text survives an id change, so keeping the pair makes the flow
    resilient to either edit alone.
    """
    if step.resource_id and step.text:
        return {"id": step.resource_id, "text": step.text}
    if step.resource_id:
        return {"id": step.resource_id}
    if step.text:
        return step.text
    return None


def render_step(step: RecordedStep) -> list[str]:
    """One step to YAML lines, already indented for the flow body."""
    selector = _selector(step)

    if step.action == "launch":
        return [f"{INDENT}- launchApp"]

    if step.action == "back":
        return [f"{INDENT}- back"]

    if step.action == "tap":
        if selector is None:
            raise ToolError("A tap step needs `text` or `resource_id` to target.")
        return [f"{INDENT}- tapOn: {_yaml_scalar(selector)}"]

    if step.action == "input":
        if not step.value:
            raise ToolError("An input step needs `value` to type.")
        lines: list[str] = []
        if selector is not None:
            lines.append(f"{INDENT}- tapOn: {_yaml_scalar(selector)}")
        lines.append(f"{INDENT}- inputText: {_yaml_scalar(step.value)}")
        return lines

    if step.action in ("assert_visible", "assert_not_visible"):
        if selector is None:
            raise ToolError(f"A {step.action} step needs `text` or `resource_id`.")
        command = "assertVisible" if step.action == "assert_visible" else "assertNotVisible"
        return [f"{INDENT}- {command}: {_yaml_scalar(selector)}"]

    if step.action == "scroll":
        return [f"{INDENT}- scroll"]

    if step.action == "scroll_until":
        if selector is None:
            raise ToolError("A scroll_until step needs `text` or `resource_id` to look for.")
        lines = [
            f"{INDENT}- scrollUntilVisible:",
            f"{INDENT}{INDENT}element: {_yaml_scalar(selector)}",
            f"{INDENT}{INDENT}direction: {step.direction or 'DOWN'}",
        ]
        if step.timeout_ms:
            lines.append(f"{INDENT}{INDENT}timeout: {step.timeout_ms}")
        return lines

    if step.action == "swipe":
        return [
            f"{INDENT}- swipe:",
            f"{INDENT}{INDENT}direction: {step.direction or 'LEFT'}",
        ]

    # wait
    if selector is not None and step.timeout_ms:
        return [
            f"{INDENT}- extendedWaitUntil:",
            f"{INDENT}{INDENT}visible: {_yaml_scalar(selector)}",
            f"{INDENT}{INDENT}timeout: {step.timeout_ms}",
        ]
    return [f"{INDENT}- waitForAnimationToEnd"]


def build_flow(
    app_id: str,
    steps: list[RecordedStep],
    assertions: list[str] | None = None,
) -> str:
    """Assemble a complete Maestro flow document."""
    lines: list[str] = [f"appId: {app_id}", "---"]
    for step in steps:
        lines.extend(render_step(step))
    lines.extend(assertions or [])
    return "\n".join(lines) + "\n"


def _assertions_from_screen(root, limit: int = MAX_GENERATED_ASSERTIONS) -> list[str]:
    """Assertions describing the screen the flow left behind.

    Capped and filtered to interactive elements. A screen carries dozens of
    labels, and a generated flow that asserts on all of them breaks the next time
    someone edits copy — which teaches people to delete the assertions rather than
    fix them. Trading coverage for survival is the right way round.
    """
    elements = evidence.describe_screen(root, limit=limit * 4)
    interactive = [element for element in elements if element.get("clickable")]
    pool = interactive or elements

    chosen: list[str] = []
    seen: set[str] = set()

    for element in pool:
        label = str(element.get("text") or element.get("content_desc") or "")
        if not label or label in seen or len(label) > 40:
            continue
        seen.add(label)
        chosen.append(f"{INDENT}- assertVisible: {_yaml_scalar(label)}")
        if len(chosen) >= limit:
            break

    return chosen


def _verdict(result: maestro_cli.FlowResult | None, flow_path: Path) -> str:
    if result is None:
        return f"Generated {flow_path.name} from {flow_path} without running it."
    if result.success:
        return f"Generated and verified {flow_path.name}; it ran green on {result.device}."
    detail = f" Failing step(s): {', '.join(result.failed_steps)}." if result.failed_steps else ""
    return (
        f"Generated {flow_path.name}, but it did not run clean.{detail} The steps describe an "
        "exploration that does not replay as written — a timing gap, or a step the agent "
        "performed differently than it recorded."
    )


def explore_and_record(
    app_id: Annotated[str, Field(description="Maestro appId, for example com.example.app.")],
    steps: Annotated[
        list[RecordedStep],
        Field(min_length=1, description="Interactions to replay, in the order they happened."),
    ],
    name: Annotated[str, Field(description="Flow name, used as the file name.")] = "recorded-flow",
    output_dir: Annotated[
        str | None,
        Field(
            description="Directory for the .yaml file. Defaults to the server working directory."
        ),
    ] = None,
    device: Annotated[
        str | None, Field(description="Device serial. Omit to take any single free device.")
    ] = None,
    capture_assertions: Annotated[
        bool,
        Field(
            description="Generate assertVisible entries from the screen the steps leave behind. "
            "Requires one exploratory run of the steps."
        ),
    ] = True,
    validate: Annotated[
        bool, Field(description="Run the generated flow to prove it replays.")
    ] = True,
    timeout: Annotated[int, Field(ge=10, le=7200, description="Flow timeout in seconds.")] = 600,
) -> dict:
    """Generate a replayable Maestro flow from a described exploration, then verify it runs.

    With both ``capture_assertions`` and ``validate`` enabled the steps run twice:
    once to observe the resulting screen and derive assertions, once to prove the
    finished file replays. That is deliberate — a flow that has never been
    executed is a hypothesis, not a test.
    """
    safe_name = _UNSAFE_NAME_RE.sub("-", name).strip("-.") or "recorded-flow"
    target_dir = Path(output_dir).expanduser() if output_dir else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)
    flow_path = target_dir / f"{safe_name}.yaml"

    flow_path.write_text(build_flow(app_id, steps), encoding="utf-8")
    logger.info("draft flow written to %s", flow_path)

    assertions: list[str] = []
    notes: list[str] = []
    probe_run: maestro_cli.FlowResult | None = None
    validation_run: maestro_cli.FlowResult | None = None

    with POOL.lease(count=1, preferred=[device] if device else None) as lease:
        serial = lease.devices[0]

        if capture_assertions:
            probe_run = maestro_cli.run_flow(flow_path, device=serial, timeout=timeout)
            if not probe_run.success:
                notes.append(
                    "The exploratory run failed, so no assertions were generated. Fix the steps "
                    "first: assertions derived from a broken screen would encode the bug."
                )
            else:
                try:
                    root = adb.dump_hierarchy(serial)
                except MaestroPlusError as exc:
                    notes.append(f"No assertions were generated: {exc}")
                else:
                    assertions = _assertions_from_screen(root)
                    if assertions:
                        flow_path.write_text(
                            build_flow(app_id, steps, assertions), encoding="utf-8"
                        )
                        notes.append(
                            f"Added {len(assertions)} assertion(s) read from the resulting screen."
                        )
                    else:
                        notes.append(
                            "No interactive labels were found on the resulting screen, so no "
                            "assertions were added."
                        )

        if validate:
            validation_run = maestro_cli.run_flow(flow_path, device=serial, timeout=timeout)

    final_run = validation_run or probe_run

    return {
        "flow_path": str(flow_path),
        "app_id": app_id,
        "step_count": len(steps),
        "assertion_count": len(assertions),
        "generated_yaml": flow_path.read_text(encoding="utf-8"),
        "probe_run": probe_run.to_dict() if probe_run else None,
        "validation_run": validation_run.to_dict() if validation_run else None,
        "validated": bool(validation_run and validation_run.success),
        "notes": notes,
        "verdict": _verdict(final_run, flow_path),
    }


__all__ = ["RecordedStep", "build_flow", "explore_and_record", "render_step"]
