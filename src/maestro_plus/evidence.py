"""Evidence collection and de-duplication.

A failed mobile run produces a pile of artifacts, and most of them are noise. Two
hundred screenshots of a screen that never changed are not evidence — they are a
storage bill and a distraction for whatever reads the report next.

De-duplication here is perceptual rather than byte-level, because the duplicate
that matters is not the identical frame. It is the frame that differs only by a
ticking clock or a battery percentage, which byte comparison calls unique and a
human calls the same screen.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .errors import MaestroPlusError

logger = logging.getLogger(__name__)

HASH_WIDTH = 8
DUPLICATE_DISTANCE = 5
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_LOG_MARKERS = (
    "FATAL EXCEPTION",
    "ANR in",
    "AndroidRuntime",
    "beginning of crash",
    "Exception",
    "NullPointerException",
    "SecurityException",
    "TimeoutException",
)

# --------------------------------------------------------------------------- #
# Screenshots
# --------------------------------------------------------------------------- #


def _file_digest(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_hash(path: Path, size: int = HASH_WIDTH) -> int:
    """Return a 64-bit difference hash of an image.

    Each pixel is compared with the one to its right and one bit is kept per
    comparison, so the hash describes gradient structure rather than absolute
    brightness. That is what lets it survive a changed clock or a new
    notification while still separating two genuinely different screens.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MaestroPlusError(
            "Image comparison needs Pillow. Install it with `pip install Pillow`."
        ) from exc

    with Image.open(path) as image:
        grey = image.convert("L").resize((size + 1, size), Image.LANCZOS)
        pixels = list(grey.getdata())

    bits = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return bits


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two hashes."""
    return bin(a ^ b).count("1")


@dataclass
class Frame:
    """One screenshot and its relationship to the frames before it."""

    path: Path
    digest: str
    phash: int | None = None
    duplicate_of: Path | None = None

    @property
    def is_unique(self) -> bool:
        return self.duplicate_of is None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "unique": self.is_unique,
            "duplicate_of": str(self.duplicate_of) if self.duplicate_of else None,
        }


def _safe_phash(path: Path) -> int | None:
    """Hash an image, returning None when it cannot be read.

    A corrupt or half-written PNG downgrades de-duplication for that one frame.
    It never fails the run.
    """
    try:
        return perceptual_hash(path)
    except (MaestroPlusError, OSError) as exc:
        logger.debug("could not hash %s: %s", path, exc)
        return None


def dedupe_screenshots(
    paths: Sequence[Path], max_distance: int = DUPLICATE_DISTANCE
) -> list[Frame]:
    """Collapse a sequence of screenshots, keeping the first of each distinct state.

    Byte-identical files are dropped first because a digest is free. The
    perceptual pass then compares against the states already kept rather than
    against the previous frame, so a screen that flickers back and forth does not
    re-enter the list every time it returns.
    """
    by_digest: dict[str, Path] = {}
    kept: list[Frame] = []
    frames: list[Frame] = []

    for path in paths:
        if not path.exists():
            continue

        digest = _file_digest(path)
        original = by_digest.get(digest)
        if original is not None:
            frames.append(Frame(path=path, digest=digest, duplicate_of=original))
            continue
        by_digest[digest] = path

        phash = _safe_phash(path)
        duplicate_of: Path | None = None
        if phash is not None:
            for candidate in kept:
                if candidate.phash is not None and hamming(phash, candidate.phash) <= max_distance:
                    duplicate_of = candidate.path
                    break

        frame = Frame(path=path, digest=digest, phash=phash, duplicate_of=duplicate_of)
        frames.append(frame)
        if duplicate_of is None:
            kept.append(frame)

    dropped = len(frames) - len(kept)
    if dropped:
        logger.info("de-duplication dropped %d of %d screenshots", dropped, len(frames))
    return frames


def unique_paths(frames: Sequence[Frame]) -> list[Path]:
    """The screenshots worth attaching to a report."""
    return [frame.path for frame in frames if frame.is_unique]


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #


def relevant_log_lines(
    raw: str,
    *,
    markers: Sequence[str] = _LOG_MARKERS,
    context: int = 5,
    max_lines: int = 200,
) -> str:
    """Keep only the log lines near something that looks like a fault.

    A full logcat buffer buries the four interesting lines under a thousand lines
    of framework chatter. Adjacent match regions are merged so a stack trace does
    not print its own header five times.
    """
    lines = raw.splitlines()
    if not lines:
        return ""

    wanted: set[int] = set()
    for index, line in enumerate(lines):
        if any(marker in line for marker in markers):
            start = max(0, index - context)
            end = min(len(lines), index + context + 1)
            wanted.update(range(start, end))

    if not wanted:
        return "\n".join(lines[-max_lines:])

    ordered = sorted(wanted)
    output: list[str] = []
    previous: int | None = None
    for index in ordered:
        if previous is not None and index != previous + 1:
            output.append(f"... {index - previous - 1} line(s) omitted ...")
        output.append(lines[index])
        previous = index

    if len(output) > max_lines:
        head = output[: max_lines // 2]
        tail = output[-(max_lines // 2) :]
        return "\n".join([*head, f"... {len(output) - max_lines} lines omitted ...", *tail])
    return "\n".join(output)


# --------------------------------------------------------------------------- #
# View hierarchy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ElementInfo:
    """One node from a uiautomator dump, reduced to what assertion work needs."""

    text: str
    resource_id: str
    content_desc: str
    class_name: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    enabled: bool

    @property
    def is_visible(self) -> bool:
        """uiautomator reports an invisible node's bounds as [0,0][0,0]."""
        if self.bounds is None:
            return False
        left, top, right, bottom = self.bounds
        return right > left and bottom > top

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.class_name

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "class": self.class_name,
            "bounds": list(self.bounds) if self.bounds else None,
            "clickable": self.clickable,
            "enabled": self.enabled,
        }


def parse_bounds(raw: str | None) -> tuple[int, int, int, int] | None:
    """Parse uiautomator's ``[x1,y1][x2,y2]`` bounds notation."""
    if not raw:
        return None
    match = _BOUNDS_RE.search(raw)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))


def iter_elements(root: ElementTree.Element) -> Iterator[ElementInfo]:
    """Walk a uiautomator tree, skipping nodes with nothing to identify them."""
    for node in root.iter("node"):
        info = ElementInfo(
            text=node.get("text", ""),
            resource_id=node.get("resource-id", ""),
            content_desc=node.get("content-desc", ""),
            class_name=node.get("class", ""),
            bounds=parse_bounds(node.get("bounds")),
            clickable=node.get("clickable") == "true",
            enabled=node.get("enabled") == "true",
        )
        if info.text or info.resource_id or info.content_desc:
            yield info


def find_elements(
    root: ElementTree.Element,
    *,
    text: str | None = None,
    resource_id: str | None = None,
    content_desc: str | None = None,
    contains: bool = True,
    visible_only: bool = True,
) -> list[ElementInfo]:
    """Find elements matching every supplied criterion.

    Substring matching is the default because Maestro flows are written against
    visible labels, and asserting on an exact string is how locators break when a
    designer adds a period.
    """

    def matches(value: str, wanted: str) -> bool:
        if contains:
            return wanted.lower() in value.lower()
        return value.lower() == wanted.lower()

    results: list[ElementInfo] = []
    for element in iter_elements(root):
        if visible_only and not element.is_visible:
            continue
        if text is not None and not matches(element.text, text):
            continue
        if resource_id is not None and not matches(element.resource_id, resource_id):
            continue
        if content_desc is not None and not matches(element.content_desc, content_desc):
            continue
        results.append(element)
    return results


def describe_screen(root: ElementTree.Element, limit: int = 60) -> list[dict[str, object]]:
    """A compact, agent-readable inventory of what is on screen.

    Interactive elements come first because an agent deciding where to tap cares
    about those, and a truncated list that starts with static text is useless.
    """
    elements = [element for element in iter_elements(root) if element.is_visible]
    elements.sort(key=lambda element: (not element.clickable, not element.enabled))
    return [element.to_dict() for element in elements[:limit]]


def screen_digest(root: ElementTree.Element) -> str:
    """Stable digest of a screen's element inventory, for cheap equality checks."""
    signature = "|".join(
        sorted(
            f"{element.resource_id}:{element.text}:{element.class_name}"
            for element in iter_elements(root)
        )
    )
    return hashlib.sha1(signature.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


__all__ = [
    "ElementInfo",
    "Frame",
    "dedupe_screenshots",
    "describe_screen",
    "find_elements",
    "hamming",
    "iter_elements",
    "parse_bounds",
    "perceptual_hash",
    "relevant_log_lines",
    "screen_digest",
    "unique_paths",
]
