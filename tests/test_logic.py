"""Tests for the pure logic: YAML generation, evidence handling, attribution.

None of these need adb, a Maestro CLI, or a device. That is the point. The parts
of this project that decide things — what a flow says, which screenshots are
duplicates, who gets blamed for a failure — have to be verifiable on any machine,
including CI, where no device exists.
"""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from maestro_plus import evidence
from maestro_plus.errors import ToolError
from maestro_plus.tools import record

HIERARCHY_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.app" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2340]">
    <node index="0" text="Sign in" resource-id="com.example.app:id/sign_in" class="android.widget.Button" package="com.example.app" content-desc="Sign in button" clickable="true" enabled="true" bounds="[120,1600][960,1720]" />
    <node index="1" text="Forgot password?" resource-id="com.example.app:id/forgot" class="android.widget.TextView" package="com.example.app" content-desc="" clickable="false" enabled="true" bounds="[300,1780][780,1840]" />
    <node index="2" text="Hidden footer" resource-id="com.example.app:id/footer" class="android.widget.TextView" package="com.example.app" content-desc="" clickable="false" enabled="true" bounds="[0,0][0,0]" />
    <node index="3" text="Continue" resource-id="com.example.app:id/continue" class="android.widget.Button" package="com.example.app" content-desc="" clickable="true" enabled="false" bounds="[120,1900][960,2020]" />
  </node>
</hierarchy>
"""


@pytest.fixture
def hierarchy() -> ElementTree.Element:
    return ElementTree.fromstring(HIERARCHY_XML)


# --------------------------------------------------------------------------- #
# Flow generation
# --------------------------------------------------------------------------- #


def _parse_yaml(text: str) -> object:
    yaml = pytest.importorskip("yaml", reason="pyyaml is a test-only dependency")
    return yaml.safe_load(text)


def test_build_flow_produces_parseable_yaml() -> None:
    flow = record.build_flow(
        "com.example.app",
        [
            record.RecordedStep(action="launch"),
            record.RecordedStep(action="tap", text="Sign in"),
            record.RecordedStep(action="input", resource_id="com.example.app:id/email", value="a@b.c"),
            record.RecordedStep(action="assert_visible", text="Welcome"),
        ],
    )

    parsed = _parse_yaml(flow)
    assert parsed["appId"] == "com.example.app"
    assert isinstance(parsed[None], list)
    assert parsed[None][0] == "launchApp"


def test_input_step_taps_the_target_before_typing() -> None:
    lines = record.render_step(
        record.RecordedStep(action="input", text="Email", value="someone@example.com")
    )

    assert any("tapOn" in line for line in lines), "typing without focusing the field does nothing"
    assert lines.index(next(line for line in lines if "tapOn" in line)) < lines.index(
        next(line for line in lines if "inputText" in line)
    )


def test_labels_containing_yaml_metacharacters_survive_round_trip() -> None:
    tricky = 'Sign in: "now" or later'
    flow = record.build_flow(
        "com.example.app", [record.RecordedStep(action="tap", text=tricky)]
    )

    _, body = _flow_parts(flow)

    assert body[0] == {"tapOn": tricky}, "a colon inside a label must not break the document"


def test_tap_without_a_target_is_rejected() -> None:
    with pytest.raises(ToolError, match="text.*resource_id"):
        record.render_step(record.RecordedStep(action="tap"))


def test_input_without_a_value_is_rejected() -> None:
    with pytest.raises(ToolError, match="value"):
        record.render_step(record.RecordedStep(action="input", text="Email"))


# --------------------------------------------------------------------------- #
# Hierarchy parsing
# --------------------------------------------------------------------------- #


def test_parse_bounds_reads_uiautomator_notation() -> None:
    assert evidence.parse_bounds("[120,1600][960,1720]") == (120, 1600, 960, 1720)
    assert evidence.parse_bounds("[0,0][0,0]") == (0, 0, 0, 0)
    assert evidence.parse_bounds("") is None
    assert evidence.parse_bounds("garbage") is None


def test_find_elements_is_case_insensitive_by_default(hierarchy: ElementTree.Element) -> None:
    assert evidence.find_elements(hierarchy, text="sign in")


def test_find_elements_exact_mode_still_works(hierarchy: ElementTree.Element) -> None:
    assert evidence.find_elements(hierarchy, text="sign in", contains=False) == []
    assert evidence.find_elements(hierarchy, text="Sign in", contains=False)


def test_invisible_nodes_are_excluded_by_default(hierarchy: ElementTree.Element) -> None:
    assert evidence.find_elements(hierarchy, text="Hidden footer") == []
    assert evidence.find_elements(hierarchy, text="Hidden footer", visible_only=False)


def test_searching_by_content_desc_works(hierarchy: ElementTree.Element) -> None:
    assert evidence.find_elements(hierarchy, content_desc="Sign in button")


def test_describe_screen_puts_interactive_elements_first(hierarchy: ElementTree.Element) -> None:
    described = evidence.describe_screen(hierarchy)
    assert described[0]["clickable"] is True


def test_screen_digest_ignores_bounds(hierarchy: ElementTree.Element) -> None:
    shifted = ElementTree.fromstring(HIERARCHY_XML.replace("[120,1600]", "[125,1605]"))
    assert evidence.screen_digest(hierarchy) == evidence.screen_digest(shifted)


# --------------------------------------------------------------------------- #
# Log triage
# --------------------------------------------------------------------------- #

NOISY_LOG = "\n".join(
    [f"D/SomeTag: routine chatter {index}" for index in range(50)]
    + ["E/AndroidRuntime: FATAL EXCEPTION: main", "E/AndroidRuntime: java.lang.NullPointerException"]
    + [f"D/SomeTag: more chatter {index}" for index in range(50)]
)


def test_relevant_log_lines_keeps_the_fault_and_drops_the_noise() -> None:
    trimmed = evidence.relevant_log_lines(NOISY_LOG, context=2)

    assert "FATAL EXCEPTION" in trimmed
    assert "NullPointerException" in trimmed
    assert len(trimmed.splitlines()) < 20, "triage that keeps 100 lines has triaged nothing"


def test_relevant_log_lines_falls_back_to_the_tail_when_nothing_matches() -> None:
    boring = "\n".join(f"D/Tag: line {index}" for index in range(300))
    trimmed = evidence.relevant_log_lines(boring, max_lines=10)

    assert len(trimmed.splitlines()) == 10
    assert "line 299" in trimmed


def test_relevant_log_lines_merges_overlapping_regions() -> None:
    log = "\n".join(
        ["D/Tag: a", "E/Tag: Exception one", "D/Tag: b", "E/Tag: Exception two", "D/Tag: c"]
    )
    trimmed = evidence.relevant_log_lines(log, context=2)

    assert trimmed.count("Exception one") == 1
    assert "omitted" not in trimmed


# --------------------------------------------------------------------------- #
# Perceptual hashing
# --------------------------------------------------------------------------- #

PNG_RED = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000010000000100802000000907753"
    "de0000001749444154789c63fcffff3f0326c84001a600620064a8000a0000"
    "ffff03005c9e3f3f0000000049454e44ae426082"
)


def test_perceptual_hash_ignores_a_shifted_copy(tmp_path) -> None:
    pytest.importorskip("PIL", reason="Pillow is an optional runtime dependency")
    from PIL import Image

    image = Image.new("RGB", (64, 64), "white")
    for x in range(64):
        for y in range(32):
            image.putpixel((x, y), (0, 0, 0))

    left = tmp_path / "left.png"
    right = tmp_path / "right.png"
    image.save(left)
    image.crop((0, 0, 63, 64)).resize((64, 64)).save(right)

    assert evidence.hamming(evidence.perceptual_hash(left), evidence.perceptual_hash(right)) <= 5


def test_hamming_counts_differing_bits() -> None:
    assert evidence.hamming(0b1010, 0b1010) == 0
    assert evidence.hamming(0b1010, 0b0101) == 4


def test_identical_files_are_dropped_before_hashing(tmp_path) -> None:
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(PNG_RED)
    second.write_bytes(PNG_RED)

    frames = evidence.dedupe_screenshots([first, second])

    assert len(frames) == 2
    assert frames[0].is_unique
    assert frames[1].duplicate_of == first
    assert evidence.unique_paths(frames) == [first]


def test_missing_files_are_skipped_without_raising(tmp_path) -> None:
    real = tmp_path / "real.png"
    real.write_bytes(PNG_RED)

    frames = evidence.dedupe_screenshots([tmp_path / "ghost.png", real])

    assert [frame.path for frame in frames] == [real]


# --------------------------------------------------------------------------- #
# Assertion generation
# --------------------------------------------------------------------------- #


def test_generated_assertions_prefer_interactive_elements(hierarchy: ElementTree.Element) -> None:
    lines = record._assertions_from_screen(hierarchy)

    assert any("Sign in" in line for line in lines)
    assert not any("Hidden footer" in line for line in lines), "asserting on the invisible"


def test_generated_assertions_respect_the_cap(hierarchy: ElementTree.Element) -> None:
    assert len(record._assertions_from_screen(hierarchy, limit=1)) <= 1
