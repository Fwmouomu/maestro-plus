"""Tests for the tool surface and the attribution rules.

The attribution tests carry the most weight. The rules are ordered, and getting
that order wrong means a crash gets reported as a locator problem — which files a
bug against the wrong team and destroys trust in the tool. That is precisely the
outcome this project exists to prevent, so it is the outcome most worth guarding.

These tests import the MCP SDK and need `pip install -e ".[dev]"` to run.
"""

from __future__ import annotations

from xml.etree import ElementTree

import pytest
from mcp import Client

from maestro_plus import report
from maestro_plus.backends.maestro_cli import FlowResult
from maestro_plus.server import TOOLS, mcp
from maestro_plus.tools import diagnostics

HIERARCHY = ElementTree.fromstring("""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.app" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2340]">
    <node index="0" text="Sign in" resource-id="com.example.app:id/sign_in" class="android.widget.Button" package="com.example.app" content-desc="" clickable="true" enabled="true" bounds="[120,1600][960,1720]" />
    <node index="1" text="Hidden footer" resource-id="com.example.app:id/footer" class="android.widget.TextView" package="com.example.app" content-desc="" clickable="false" enabled="true" bounds="[0,0][0,0]" />
    <node index="2" text="Continue" resource-id="com.example.app:id/continue" class="android.widget.Button" package="com.example.app" content-desc="" clickable="true" enabled="false" bounds="[120,1900][960,2020]" />
  </node>
</hierarchy>
""")

EXPECTED_TOOLS = {
    "health_check",
    "list_device_pool",
    "run_parallel",
    "run_and_assert",
    "assert_visual",
    "debug_failure",
    "explore_and_record",
}


# --------------------------------------------------------------------------- #
# Tool surface
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_server_exposes_exactly_the_documented_tools() -> None:
    async with Client(mcp) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}

    assert tools == EXPECTED_TOOLS
    assert len(TOOLS) == len(EXPECTED_TOOLS)


@pytest.mark.anyio
async def test_official_maestro_tools_are_not_duplicated() -> None:
    """The design rule from docs/why.md, enforced as a test.

    Reimplementing an upstream tool is the specific way this project would stop
    being worth maintaining. A build should catch that, not a reviewer's memory.
    """
    async with Client(mcp) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}

    for upstream in ("list_devices", "inspect_screen", "take_screenshot", "run", "cheat_sheet"):
        assert upstream not in tools, f"{upstream} duplicates the official Maestro server"


@pytest.mark.anyio
async def test_every_tool_documents_itself_for_the_model() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools

    undocumented = [tool.name for tool in tools if not (tool.description or "").strip()]
    assert not undocumented, f"a tool without a docstring is invisible to the model: {undocumented}"


@pytest.mark.anyio
async def test_required_arguments_land_in_every_tool_schema() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert "flows" in tools["run_parallel"].input_schema.get("required", [])
    assert "flow" in tools["run_and_assert"].input_schema.get("required", [])
    assert not tools["health_check"].input_schema.get("required"), (
        "health_check must be callable with no arguments, because it is the first thing to try"
    )


# --------------------------------------------------------------------------- #
# Attribution rules
# --------------------------------------------------------------------------- #


def _failure(**overrides: object) -> FlowResult:
    base: dict[str, object] = {
        "flow": "login.yaml",
        "device": "emulator-5554",
        "success": False,
        "exit_code": 1,
        "duration_s": 3.0,
        "failed_steps": ['Tap on "Sign in"'],
    }
    base.update(overrides)
    return FlowResult(**base)  # type: ignore[arg-type]


def test_a_crash_outranks_every_other_signal() -> None:
    attribution, confidence, findings, _ = diagnostics._attribute(
        _failure(),
        "E/AndroidRuntime: FATAL EXCEPTION: main",
        focus="com.example.app/.MainActivity",
        hierarchy=HIERARCHY,
    )

    assert attribution == report.ATTRIBUTION_APPLICATION
    assert confidence == "high"
    assert any("FATAL EXCEPTION" in finding for finding in findings)


def test_an_anr_is_an_application_defect() -> None:
    attribution, confidence, _, _ = diagnostics._attribute(
        _failure(), "W/ActivityManager: ANR in com.example.app", focus=None, hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_APPLICATION
    assert confidence == "high"


def test_a_permission_dialog_is_a_test_defect() -> None:
    attribution, confidence, _, _ = diagnostics._attribute(
        _failure(),
        "",
        focus="com.android.permissioncontroller/.GrantPermissionsActivity",
        hierarchy=HIERARCHY,
    )

    assert attribution == report.ATTRIBUTION_TEST
    assert confidence == "high"


def test_a_system_ui_surface_is_an_environment_problem() -> None:
    attribution, _, _, _ = diagnostics._attribute(
        _failure(), "", focus="com.android.systemui/.shade.NotificationShadeWindow", hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_ENVIRONMENT


def test_a_missing_locator_stays_honestly_unknown() -> None:
    attribution, confidence, findings, _ = diagnostics._attribute(
        _failure(failed_steps=['Tap on "Checkout"']),
        "",
        focus="com.example.app/.MainActivity",
        hierarchy=HIERARCHY,
    )

    assert attribution == report.ATTRIBUTION_UNKNOWN
    assert confidence == "low"
    assert any("cannot separate them" in finding for finding in findings)


def test_a_disabled_element_blames_the_application() -> None:
    attribution, confidence, _, _ = diagnostics._attribute(
        _failure(failed_steps=['Tap on "Continue"']), "", focus=None, hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_APPLICATION
    assert confidence == "medium"


def test_an_unrendered_element_blames_the_application() -> None:
    attribution, _, findings, _ = diagnostics._attribute(
        _failure(failed_steps=['Tap on "Hidden footer"']), "", focus=None, hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_APPLICATION
    assert any("zero-area" in finding for finding in findings)


def test_a_reachable_element_points_at_the_test() -> None:
    attribution, confidence, _, recommendations = diagnostics._attribute(
        _failure(failed_steps=['Tap on "Sign in"']), "", focus=None, hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_TEST
    assert confidence == "medium"
    assert any("wait" in recommendation for recommendation in recommendations)


def test_an_unreadable_hierarchy_is_an_environment_problem() -> None:
    attribution, confidence, _, _ = diagnostics._attribute(
        _failure(), "", focus=None, hierarchy=None
    )

    assert attribution == report.ATTRIBUTION_ENVIRONMENT
    assert confidence == "medium"


def test_attribution_never_raises_on_an_unrecognised_failure() -> None:
    """A diagnosis tool that crashes on a novel failure is worse than one that shrugs."""
    attribution, confidence, findings, recommendations = diagnostics._attribute(
        _failure(failed_steps=[]), "", focus=None, hierarchy=HIERARCHY
    )

    assert attribution == report.ATTRIBUTION_UNKNOWN
    assert confidence == "low"
    assert findings and recommendations


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def test_report_escapes_untrusted_content() -> None:
    diagnosis = report.Diagnosis(
        flow="<script>alert(1)</script>.yaml",
        device=None,
        verdict="failed",
        findings=["<img src=x onerror=alert(1)>"],
    )

    page = report.render_html(diagnosis)

    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_report_shows_the_attribution_and_the_reproduce_command() -> None:
    diagnosis = report.Diagnosis(
        flow="login.yaml",
        device="emulator-5554",
        verdict="The app crashed.",
        attribution=report.ATTRIBUTION_APPLICATION,
        confidence="high",
    )

    page = report.render_html(diagnosis)

    assert "The app crashed." in page
    assert "application defect" in page
    assert "maestro test login.yaml --device emulator-5554" in page


def test_bundle_writes_both_formats(tmp_path) -> None:
    diagnosis = report.Diagnosis(flow="a.yaml", device=None, verdict="nope")
    written = report.write_bundle(diagnosis, tmp_path)

    assert written["json"].exists()
    assert written["html"].exists()
    assert written["json"].read_text(encoding="utf-8").startswith("{")
