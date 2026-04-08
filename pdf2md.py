#!/usr/bin/env python3
"""
pdf2md.py — Convert PDF documents to Markdown using pluggable converter backends.

Supported backends:
  - docling (default): Uses IBM's Docling with RapidOCR or EasyOCR
  - (future): llamaparse, markitdown, etc.

Usage:
  # Single file
  python pdf2md.py raw/document.pdf

  # Multiple files
  python pdf2md.py raw/doc1.pdf raw/doc2.pdf

  # Entire directory
  python pdf2md.py raw/

  # Custom output directory
  python pdf2md.py raw/ --output-dir processed/

  # Use EasyOCR instead of RapidOCR
  python pdf2md.py raw/document.pdf --ocr-engine easyocr

  # Force re-processing of already converted files
  python pdf2md.py raw/ --force
"""

from __future__ import annotations

import abc
import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

log = logging.getLogger("pdf2md")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tiff"}

# Graceful shutdown flag and active subprocess tracking
_shutdown_requested = False
_active_proc: Optional[subprocess.Popen] = None


def _signal_handler(signum: int, frame) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        log.warning("Second interrupt received — forcing exit")
        # Force-kill any remaining subprocess tree
        if _active_proc and _active_proc.poll() is None:
            try:
                os.killpg(os.getpgid(_active_proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        sys.exit(1)
    _shutdown_requested = True
    log.warning(
        "Interrupt received — stopping. Press Ctrl-C again to force quit."
    )
    # Kill the active subprocess tree immediately
    if _active_proc and _active_proc.poll() is None:
        try:
            pgid = os.getpgid(_active_proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ConversionStats:
    """Statistics for a single file conversion."""
    input_file: str = ""
    input_size_bytes: int = 0
    output_file: str = ""
    output_size_bytes: int = 0
    output_lines: int = 0
    num_headings: int = 0
    num_table_rows: int = 0
    num_images: int = 0
    num_image_files: int = 0
    elapsed_seconds: float = 0.0
    success: bool = False
    error: str = ""
    skipped: bool = False
    converter: str = ""


@dataclass
class RunSummary:
    """Summary of an entire run."""
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    total_elapsed_seconds: float = 0.0
    file_stats: list[ConversionStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def detect_num_threads() -> int:
    """Detect available CPU threads and return max(1, count - 1)."""
    try:
        count = os.cpu_count()
        if count is None:
            log.warning("Could not detect CPU count, defaulting to 1 thread")
            return 1
        threads = max(1, count - 1)
        log.info(f"Detected {count} CPUs, using {threads} thread(s)")
        return threads
    except Exception:
        log.warning("Error detecting CPU count, defaulting to 1 thread")
        return 1


def get_available_memory_gb() -> float:
    """Return available system memory in GB. Falls back to 8 if unknown."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    log.warning("Could not detect available memory, assuming 8 GB")
    return 8.0


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get page count from a PDF without loading the full document.

    Uses a lightweight scan of the PDF trailer/cross-reference for the page
    count. Falls back to 0 if the count cannot be determined.
    """
    try:
        # Try pymupdf if available (fast, accurate)
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            return len(doc)
    except ImportError:
        pass

    # Fallback: scan for /Type /Page entries (rough estimate)
    try:
        data = pdf_path.read_bytes()
        # Count /Type /Page (but not /Type /Pages which is the parent)
        import re as _re
        count = len(_re.findall(rb"/Type\s*/Page(?!s)", data))
        return count if count > 0 else 0
    except OSError:
        return 0


# Docling base memory: ~4 GB for all models (layout, table, OCR, picture
# classifier, CodeFormula VLM) loaded simultaneously.
_DOCLING_BASE_MEMORY_GB = 4.0

# Per-thread overhead: each thread holds page images, layout tensors, table
# structure intermediaries, and OCR buffers. Empirically measured at ~1-1.5 GB
# per active thread on complex spec documents.
_DOCLING_PER_THREAD_GB = 1.2


def compute_safe_threads(page_count: int, available_mem_gb: float) -> int:
    """Compute a safe thread count that avoids OOM for a given document.

    Docling loads ~4 GB of models into RAM, then each processing thread
    adds ~1.2 GB of working memory for page images, layout inference,
    table structure, and OCR buffers. We leave a 2 GB buffer for the
    OS and other processes.

    Empirical baseline on 16 GB system:
      - 250 pages (CXL 1.1):  OK at 7 threads  (~12 GB peak)
      - 628 pages (CXL 2.0):  OOM at 7 threads  (>16 GB peak)
      - 628 pages at 4 threads: ~8.8 GB peak → safe
    """
    cpu_threads = max(1, (os.cpu_count() or 2) - 1)
    usable_gb = available_mem_gb - _DOCLING_BASE_MEMORY_GB - 2.0  # OS buffer

    if usable_gb <= 0:
        return 1

    max_by_memory = max(1, int(usable_gb / _DOCLING_PER_THREAD_GB))
    safe = min(cpu_threads, max_by_memory)
    return max(1, safe)


def is_pdf(path: Path) -> bool:
    """Check if a file is a PDF by extension and magic bytes."""
    if path.suffix.lower() != ".pdf":
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(8)
            return header[:5] == b"%PDF-"
    except (OSError, IOError):
        return False


def collect_pdf_files(inputs: list[str]) -> list[Path]:
    """Resolve input arguments to a deduplicated, sorted list of PDF paths.

    Directories are scanned recursively for PDF files.
    Non-PDF files are skipped with a warning.
    """
    pdf_files: list[Path] = []
    seen: set[Path] = set()

    for item in inputs:
        p = Path(item).resolve()
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and is_pdf(child) and child not in seen:
                    pdf_files.append(child)
                    seen.add(child)
        elif p.is_file():
            if not is_pdf(p):
                log.warning(f"Skipping non-PDF file: {p}")
                continue
            if p not in seen:
                pdf_files.append(p)
                seen.add(p)
        else:
            log.error(f"Path does not exist: {item}")

    return pdf_files


CONFIRM_THRESHOLD = 5  # ask for confirmation when more than this many files


def confirm_large_batch(pdf_files: list[Path]) -> bool:
    """Show batch summary and ask user to confirm when file count exceeds threshold."""
    total_size = sum(f.stat().st_size for f in pdf_files)
    count = len(pdf_files)

    print(f"\n  WARNING: About to process {count} PDF file(s) "
          f"({format_size(total_size)} total).")
    print(f"  This may take a long time on CPU. You can press Ctrl-C at any "
          f"time to stop.\n")

    try:
        answer = input("  Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    return answer in ("y", "yes")


def compute_output_dir(pdf_path: Path, base_output_dir: Optional[Path]) -> Path:
    """Determine the output directory for a given PDF file."""
    stem = pdf_path.stem
    # Sanitize: replace spaces and special chars with underscores
    safe_name = re.sub(r"[^\w\-.]", "_", stem)
    if base_output_dir:
        return base_output_dir / safe_name
    else:
        return pdf_path.parent / "processed" / safe_name


def compute_markdown_stats(md_path: Path, figures_dir: Optional[Path]) -> dict:
    """Compute statistics from a generated Markdown file."""
    stats = {
        "output_size_bytes": 0,
        "output_lines": 0,
        "num_headings": 0,
        "num_table_rows": 0,
        "num_images": 0,
        "num_image_files": 0,
    }
    if not md_path.exists():
        return stats

    stats["output_size_bytes"] = md_path.stat().st_size
    try:
        content = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return stats

    lines = content.splitlines()
    stats["output_lines"] = len(lines)
    stats["num_headings"] = sum(1 for line in lines if line.startswith("## "))
    stats["num_table_rows"] = sum(1 for line in lines if line.startswith("| "))
    stats["num_images"] = len(re.findall(r"!\[[^\]]*\]\[(?:fig|img)\d+\]", content))

    # Count image files on disk
    if figures_dir and figures_dir.exists():
        stats["num_image_files"] = sum(
            1 for f in figures_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        )
    return stats


# ---------------------------------------------------------------------------
# Live progress display
# ---------------------------------------------------------------------------

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class ProgressLine:
    """Live single-line progress indicator with spinner and elapsed time.

    Parses docling stderr to extract meaningful stage names and displays
    a continuously-updating status line. The spinner runs in a background
    thread so the user sees activity even during long silent phases.
    """

    # Patterns to extract meaningful status from docling's stderr
    _STAGE_PATTERNS = [
        (re.compile(r"Initializing pipeline"), "Initializing pipeline"),
        (re.compile(r"Loading model|Loading weights|Downloading"), "Loading models"),
        (re.compile(r"Processing document"), "Processing pages"),
        (re.compile(r"Batch processed (\d+) images"), "Processing figures"),
        (re.compile(r"Finished converting"), "Finalizing"),
        (re.compile(r"writing Markdown output"), "Writing output"),
    ]

    def __init__(self):
        self._stage = "Starting"
        self._start = time.monotonic()
        self._spin_idx = 0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._is_tty = sys.stderr.isatty()

    def start(self) -> None:
        if not self._is_tty:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, clear: bool = True) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        if clear and self._is_tty:
            sys.stderr.write("\r" + " " * 80 + "\r")
            sys.stderr.flush()

    def update_from_line(self, line: str) -> None:
        """Parse a stderr line and update the stage if it matches a known pattern."""
        for pattern, stage in self._STAGE_PATTERNS:
            if pattern.search(line):
                with self._lock:
                    self._stage = stage
                break

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._render()
            self._stop_event.wait(timeout=0.15)

    def _render(self) -> None:
        elapsed = time.monotonic() - self._start
        spinner = SPINNER[self._spin_idx % len(SPINNER)]
        self._spin_idx += 1
        with self._lock:
            stage = self._stage
        line = f"\r  {spinner} {format_duration(elapsed)} — {stage}"
        # Pad to overwrite previous line, cap at terminal width
        sys.stderr.write(f"{line:<78}")
        sys.stderr.flush()


def format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m {secs:.0f}s"


# ---------------------------------------------------------------------------
# Post-processing: TOC cleanup, cross-references, and image reorganization
# ---------------------------------------------------------------------------


def _heading_to_anchor(heading_text: str) -> str:
    """Convert a markdown heading to a GitHub-flavored anchor slug.

    '## 3.2.1 CXL.cache Overview' → '321-cxlcache-overview'
    """
    text = heading_text.lower()
    text = re.sub(r"[^\w\s-]", "", text)  # strip punctuation
    text = re.sub(r"\s+", "-", text.strip())  # spaces → hyphens
    text = re.sub(r"-+", "-", text)  # collapse multiple hyphens
    return text


def _section_depth(section_num: str) -> int:
    """Determine heading depth from a section number.

    '1.0' → 1 (chapter-level, ##)
    '1.1' → 2 (section, ###)
    '2.2.1' → 3 (subsection, ####)
    '2.2.1.1' → 4 (#####)

    Treats 'N.0' as depth 1 (chapter level).
    """
    parts = section_num.split(".")
    # "1.0", "2.0" etc. are chapter-level despite having 2 parts
    if len(parts) == 2 and parts[1] == "0":
        return 1
    return len(parts)


def postprocess_heading_levels(content: str) -> str:
    """Fix heading hierarchy — docling flattens everything to ##.

    Determines correct heading level from section numbering:
      ## 1.0 Introduction     → ## 1.0 Introduction         (H2, chapter)
      ## 1.1 Audience         → ### 1.1 Audience             (H3, section)
      ## 2.2.1 Bias Model     → #### 2.2.1 Bias Model       (H4, subsection)
      ## 2.2.1.1 Host Bias    → ##### 2.2.1.1 Host Bias     (H5)
      ## Table 6. D2H Fields  → demoted one level below parent section

    Non-numbered headings (legal text, title page) stay at ##.
    """
    lines = content.splitlines()
    new_lines: list[str] = []
    current_depth = 1  # track depth of most recent numbered section

    for line in lines:
        if not line.startswith("## "):
            new_lines.append(line)
            continue

        heading_text = line[3:].strip()

        # Numbered section heading
        m = re.match(r"^(\d+(?:\.\d+)*)\s+", heading_text)
        if m:
            section_num = m.group(1)
            depth = _section_depth(section_num)
            current_depth = depth
            # depth 1 → ##, depth 2 → ###, etc.
            prefix = "#" * (depth + 1)
            new_lines.append(f"{prefix} {heading_text}")
            continue

        # Appendix sections: "A.1 Title", "A Title"
        m_app = re.match(r"^([A-Z](?:\.\d+)*)\s+", heading_text)
        if m_app:
            app_num = m_app.group(1)
            depth = len(app_num.split("."))
            current_depth = depth
            prefix = "#" * (depth + 1)
            new_lines.append(f"{prefix} {heading_text}")
            continue

        # Table/Figure headings: demote one level below current section
        m_tf = re.match(r"^(Table|Figure)\s+\d+[\.\:]", heading_text, re.IGNORECASE)
        if m_tf:
            depth = current_depth + 1
            prefix = "#" * min(depth + 1, 6)  # cap at H6
            new_lines.append(f"{prefix} {heading_text}")
            continue

        # Non-numbered heading (title page, legal, etc.) — keep as ##
        new_lines.append(line)

    result = "\n".join(new_lines)
    # Count changes
    changed = sum(1 for old, new in zip(lines, new_lines) if old != new)
    log.info(f"Fixed heading levels for {changed} headings")
    return result


def _build_heading_map(content: str) -> dict[str, str]:
    """Build a map of section numbers to their markdown heading anchors.

    Scans all heading lines (any level) and returns:
      {'1.0': '10-introduction', '3.2.1': '321-cxlcache-overview', ...}

    Also indexes 'Table N' and 'Figure N' headings when present.
    """
    heading_map: dict[str, str] = {}

    for line in content.splitlines():
        if not line.startswith("#"):
            continue
        # Strip leading #s and space
        m_heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if not m_heading:
            continue
        heading_text = m_heading.group(2).strip()

        # Match numbered sections: "1.0 Introduction", "3.2.1 Overview"
        m = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)", heading_text)
        if m:
            section_num = m.group(1)
            heading_map[section_num] = _heading_to_anchor(heading_text)
            continue

        # Match appendix sections: "A.1 Title", "Appendix A Title"
        m = re.match(r"^(?:Appendix\s+)?([A-Z](?:\.\d+)*)\s+(.+)", heading_text)
        if m:
            appendix_num = m.group(1)
            heading_map[appendix_num] = _heading_to_anchor(heading_text)
            continue

        # Match "Table N. Title" or "Figure N. Title" headings
        m = re.match(r"^(Table|Figure)\s+(\d+)[\.\:]\s*(.*)", heading_text, re.IGNORECASE)
        if m:
            kind = m.group(1)
            num = m.group(2)
            key = f"{kind} {num}"
            heading_map[key] = _heading_to_anchor(heading_text)

    return heading_map


def _clean_toc_entry(cell: str) -> tuple[str, str, str]:
    """Parse a TOC cell into (section_number, title, page_number).

    Input:  '3.2.1 CXL.cache Overview.........................................................37'
    Output: ('3.2.1', 'CXL.cache Overview', '37')

    Input:  'Flex Bus Link Features..........................18'
    Output: ('', 'Flex Bus Link Features', '18')

    Handles merged entries where two TOC lines got concatenated:
    Input:  'CXL.io.........29 PCIe Root Complex Integrated Endpoint...30'
    Output: ('', 'CXL.io', '29')  (takes the first entry)
    """
    text = cell.strip()
    if not text:
        return ("", "", "")

    page = ""

    # Handle merged entries: dot-leader+page in the middle followed by
    # another entry. Split and take only the first entry.
    # e.g. "CXL.io.........29 PCIe Root Complex Integrated Endpoint...30"
    mid_split = re.match(
        r"^(.*?)\s*[\.·…]{3,}\s*(\d+)\s+\d*[\.\d]*\s*[A-Z]", text
    )
    if mid_split:
        text = mid_split.group(1).strip()
        page = mid_split.group(2)
    else:
        # Standard case: title with dot leaders and trailing page number
        m = re.match(r"^(.*?)\s*[\.·…]{3,}\s*(\d+)\s*$", text)
        if m:
            text = m.group(1).strip()
            page = m.group(2)

    # Clean up any remaining dot sequences (3+ dots)
    text = re.sub(r"[\.·…]{3,}", " ", text).strip()

    # Extract section number from the title part
    m2 = re.match(r"^(\d+(?:\.\d+)*)\s+(.*)", text)
    if m2:
        section_num = m2.group(1)
        # Normalize bare integers to N.0 (e.g., "3" → "3.0") to match
        # how chapter headings appear in the document body
        if "." not in section_num:
            section_num = f"{section_num}.0"
        return (section_num, m2.group(2).strip(), page)

    # Appendix sections: "A.1 Title" or "A Title"
    m3 = re.match(r"^([A-Z](?:\.\d+)*)\s+(.*)", text)
    if m3:
        return (m3.group(1), m3.group(2).strip(), page)

    return ("", text, page)


def postprocess_toc(content: str, heading_map: dict[str, str]) -> str:
    """Replace the mangled TOC table with a clean 3-column table with internal links.

    Detects the region between '## Contents' and the first body heading (e.g.,
    '## Revision History' or '## 1.0 Introduction'), parses the duplicated
    table columns, and emits a clean table:

      | Section | Title | PDF Page |
      |---------|-------|----------|
      | 1.0 | [Introduction](#10-introduction) | 14 |
    """
    # Find the TOC region
    toc_start_match = re.search(r"^## Contents\s*$", content, re.MULTILINE)
    if not toc_start_match:
        log.info("No '## Contents' heading found, skipping TOC cleanup")
        return content

    # Find where body content starts (first numbered heading or "Revision History")
    toc_end_pattern = re.compile(
        r"^## (?:Revision History|\d+\.\d)", re.MULTILINE
    )
    toc_end_match = toc_end_pattern.search(content, toc_start_match.end())
    if not toc_end_match:
        log.warning("Could not find end of TOC region")
        return content

    toc_region = content[toc_start_match.end():toc_end_match.start()]

    # Parse table rows from the TOC region.
    # Docling renders the TOC as a table where each entry is duplicated
    # across 3-4 columns. Sometimes the section number is in one cell
    # and the title in the next. Strategy: join all non-empty unique cell
    # fragments into one string, then parse that.
    toc_entries: list[tuple[str, str, str, str]] = []  # (section, title, page, list_type)
    current_list_type = "contents"  # track contents vs figures vs tables

    # --- Pass 1: Extract combined text per table row + collect bare lines ---
    # Docling sometimes renders figures/tables lists as:
    #   | 1 | Title...page |   (proper table rows for first few)
    #   5                      (bare number on its own line)
    #   ...
    #   Title with dots...page (plain title line)
    # We collect bare numbers and titles separately, then pair them.
    raw_rows: list[str] = []
    current_list_type = "contents"
    list_type_per_row: list[str] = []
    bare_numbers: dict[str, list[int]] = {"figures": [], "tables": []}
    bare_titles: dict[str, list[tuple[str, str]]] = {"figures": [], "tables": []}

    for line in toc_region.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|---"):
            continue

        # Non-table lines: collect bare numbers and title lines
        if not stripped.startswith("|"):
            if current_list_type in ("figures", "tables"):
                if re.match(r"^\d+$", stripped):
                    bare_numbers[current_list_type].append(int(stripped))
                elif re.search(r"[\.·…]{3,}\s*\d+\s*$", stripped):
                    m = re.match(r"^(.*?)\s*[\.·…]{3,}\s*(\d+)\s*$", stripped)
                    if m:
                        title = re.sub(r"[\.·…]{3,}", " ", m.group(1)).strip()
                        bare_titles[current_list_type].append((title, m.group(2)))
            continue

        cells = [c.strip() for c in stripped.split("|") if c.strip()]
        if not cells:
            continue

        # Check for section header rows (Figures, Tables)
        first_cell_lower = cells[0].lower().strip()
        if first_cell_lower in ("figures", "tables"):
            current_list_type = first_cell_lower
            continue

        # De-duplicate cells: keep only unique content fragments, in order.
        # Short cells (like bare section numbers "2", "3.1") must not be
        # discarded just because their text appears as a substring of a
        # longer cell (e.g., "2" appearing inside "...page 23").
        # Only treat a cell as "already seen" if it's long enough that a
        # substring match is meaningful (>10 chars).
        unique_parts: list[str] = []
        for cell in cells:
            cell = cell.strip()
            if not cell:
                continue
            if len(cell) > 10:
                already_seen = any(cell in existing for existing in unique_parts)
            else:
                # Short cell — only skip if it's an exact match
                already_seen = cell in unique_parts
            if not already_seen:
                if len(cell) > 10:
                    unique_parts = [p for p in unique_parts if p not in cell]
                unique_parts.append(cell)

        combined = " ".join(unique_parts)
        if combined:
            raw_rows.append(combined)
            list_type_per_row.append(current_list_type)

    # Pair bare numbers with bare titles for figures/tables lists
    for lt in ("figures", "tables"):
        nums = bare_numbers[lt]
        titles = bare_titles[lt]
        if nums and titles and len(nums) == len(titles):
            label = "Figure" if lt == "figures" else "Table"
            for num, (title, page) in zip(nums, titles):
                toc_entries.append((f"{label} {num}", title, page, lt))
            log.info(
                f"Recovered {len(nums)} {lt} list entries from separated "
                f"numbers/titles"
            )
        elif nums or titles:
            log.warning(
                f"Could not pair {lt} list: {len(nums)} numbers vs "
                f"{len(titles)} titles"
            )

    # --- Pass 2: Join split entries and split merged entries ---
    # A split entry looks like:
    #   Row N:   "14.11.3.1 Error"         (section + partial title, no page)
    #   Row N+1: "Logging....244 14.11.3.2 Event Monitors....245"  (continuation)
    #
    # A merged entry has multiple "title...page" pairs in one string:
    #   "Logging....244 14.11.3.2 Event Monitors....245"
    #
    # Strategy: first join rows that look like continuations, then split
    # merged entries within each joined row.

    joined_rows: list[tuple[str, str]] = []  # (combined_text, list_type)
    i = 0
    while i < len(raw_rows):
        text = raw_rows[i]
        lt = list_type_per_row[i]

        # Check if this row has a section number but no page number
        # (indicating the title is split to the next row)
        has_section = bool(re.match(r"^\d+(?:\.\d+)*\s+", text))
        has_page = bool(re.search(r"[\.·…]{3,}\s*\d+", text))

        if has_section and not has_page and (i + 1) < len(raw_rows):
            # Peek at next row — if it doesn't start with a section number,
            # it's a continuation of this title
            next_text = raw_rows[i + 1]
            next_starts_with_section = bool(
                re.match(r"^\d+(?:\.\d+)*\s+|^[A-Z](?:\.\d+)*\s+", next_text)
            )
            if not next_starts_with_section:
                # Join with next row
                text = text + " " + next_text
                i += 1  # skip next row

        joined_rows.append((text, lt))
        i += 1

    # --- Pass 3: Split merged entries and parse into final TOC entries ---
    # A merged entry contains multiple "title...page" sequences.
    # Split on pattern: page_number followed by a section number.
    _SPLIT_MERGED_RE = re.compile(
        r"([\.·…]{3,}\s*\d+)\s+(\d+(?:\.\d+)*\s+)"
    )

    for text, lt in joined_rows:
        # Split merged entries into individual entries
        parts = _SPLIT_MERGED_RE.split(text)

        # Reassemble: parts alternates between text, dot+page, section_start
        entries_to_parse: list[str] = []
        buf = ""
        for j, part in enumerate(parts):
            if j % 3 == 0:
                buf += part
            elif j % 3 == 1:
                # This is the dot-leader + page portion
                buf += part
                entries_to_parse.append(buf.strip())
                buf = ""
            elif j % 3 == 2:
                # This is the start of the next section number
                buf = part

        if buf.strip():
            entries_to_parse.append(buf.strip())

        if not entries_to_parse:
            entries_to_parse = [text]

        for entry_text in entries_to_parse:
            if lt == "figures":
                m = re.match(r"^(\d+)\s+(.*)", entry_text)
                if m:
                    _, title, page = _clean_toc_entry(m.group(2))
                    toc_entries.append((f"Figure {m.group(1)}", title, page, lt))
                    continue
            elif lt == "tables":
                m = re.match(r"^(\d+)\s+(.*)", entry_text)
                if m:
                    _, title, page = _clean_toc_entry(m.group(2))
                    toc_entries.append((f"Table {m.group(1)}", title, page, lt))
                    continue

            sec, title, page = _clean_toc_entry(entry_text)
            if title or sec:
                toc_entries.append((sec, title, page, lt))

    if not toc_entries:
        log.warning("No TOC entries parsed, skipping TOC cleanup")
        return content

    # --- Recover missing section numbers from heading map ---
    # Build a reverse map: normalized title → section number
    # This handles cases where docling dropped the section number from
    # the TOC table but the heading exists in the document body.
    title_to_section: dict[str, str] = {}
    for line in content.splitlines():
        m_h = re.match(r"^#{1,6}\s+(.*)", line)
        if not m_h:
            continue
        heading_text = m_h.group(1).strip()
        m_sec = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)", heading_text)
        if m_sec:
            # Normalize title for fuzzy matching
            norm_title = re.sub(r"\s+", " ", m_sec.group(2).strip().lower())
            title_to_section[norm_title] = m_sec.group(1)
        m_app = re.match(r"^(?:Appendix\s+)?([A-Z](?:\.\d+)*)\s+(.+)", heading_text)
        if m_app:
            norm_title = re.sub(r"\s+", " ", m_app.group(2).strip().lower())
            title_to_section[norm_title] = m_app.group(1)

    recovered = 0
    patched_entries: list[tuple[str, str, str, str]] = []
    for sec, title, page, lt in toc_entries:
        if not sec and title and lt == "contents":
            norm = re.sub(r"\s+", " ", title.strip().lower())
            found_sec = title_to_section.get(norm, "")
            if found_sec:
                sec = found_sec
                recovered += 1
        patched_entries.append((sec, title, page, lt))

    toc_entries = patched_entries
    if recovered:
        log.info(f"Recovered {recovered} missing section numbers from document headings")

    # --- Build clean TOC with separate tables per section ---

    def _make_link(sec: str, title: str) -> str:
        """Create an internal markdown link for a TOC entry."""
        if not title:
            return ""
        anchor = heading_map.get(sec, "")
        if anchor:
            return f"[{title}](#{anchor})"
        return title

    def _make_section_link(sec: str) -> str:
        """Create a linked section number."""
        anchor = heading_map.get(sec, "")
        if anchor:
            return f"[{sec}](#{anchor})"
        return sec

    # Group entries by list type
    contents_entries = [(s, t, p) for s, t, p, lt in toc_entries if lt == "contents"]
    figures_entries = [(s, t, p) for s, t, p, lt in toc_entries if lt == "figures"]
    tables_entries = [(s, t, p) for s, t, p, lt in toc_entries if lt == "tables"]

    toc_lines: list[str] = []

    # --- Contents table ---
    toc_lines.append("## Contents\n")
    toc_lines.append("| Section | Title | PDF Page |")
    toc_lines.append("|---------|-------|----------|")
    for sec, title, page in contents_entries:
        sec_cell = _make_section_link(sec) if sec else ""
        title_cell = _make_link(sec, title)
        toc_lines.append(f"| {sec_cell} | {title_cell} | {page} |")

    # --- List of Figures table ---
    if figures_entries:
        toc_lines.append("")
        toc_lines.append("### List of Figures\n")
        toc_lines.append("| Figure | Title | PDF Page |")
        toc_lines.append("|--------|-------|----------|")
        for sec, title, page in figures_entries:
            anchor = heading_map.get(sec, "")
            if anchor:
                sec_cell = f"[{sec}](#{anchor})"
                title_cell = f"[{title}](#{anchor})" if title else ""
            else:
                sec_cell = sec
                title_cell = title
            toc_lines.append(f"| {sec_cell} | {title_cell} | {page} |")

    # --- List of Tables table ---
    if tables_entries:
        toc_lines.append("")
        toc_lines.append("### List of Tables\n")
        toc_lines.append("| Table | Title | PDF Page |")
        toc_lines.append("|-------|-------|----------|")
        for sec, title, page in tables_entries:
            anchor = heading_map.get(sec, "")
            if anchor:
                sec_cell = f"[{sec}](#{anchor})"
                title_cell = f"[{title}](#{anchor})" if title else ""
            else:
                sec_cell = sec
                title_cell = title
            toc_lines.append(f"| {sec_cell} | {title_cell} | {page} |")

    # --- Detect parsing issues ---
    all_entries = [(s, t, p) for s, t, p, _ in toc_entries]
    issues: list[str] = []
    for sec, title, page in all_entries:
        if not title or not sec:
            continue
        if re.search(r"\s\d{2,3}\s+[A-Z]", title):
            issues.append(f"Possible merged entry in {sec}: '{title}'")
        elif re.match(r"\d+\.\d+", title):
            issues.append(f"Title may include next section in {sec}: '{title}'")

    if issues:
        log.warning(
            f"TOC has {len(issues)} entries with possible parsing issues. "
            f"Please verify the Table of Contents against the source PDF."
        )
        for issue in issues[:5]:
            log.warning(f"  - {issue}")
        if len(issues) > 5:
            log.warning(f"  ... and {len(issues) - 5} more")
        toc_lines.append("")
        toc_lines.append(
            f"<!-- WARNING: {len(issues)} TOC entries may have parsing issues. "
            f"Please verify against the source PDF. -->"
        )

    toc_replacement = "\n".join(toc_lines) + "\n\n"

    # Replace the TOC region
    new_content = (
        content[:toc_start_match.start()]
        + toc_replacement
        + content[toc_end_match.start():]
    )

    total = len(contents_entries) + len(figures_entries) + len(tables_entries)
    log.info(
        f"Cleaned TOC: {len(contents_entries)} sections, "
        f"{len(figures_entries)} figures, {len(tables_entries)} tables"
    )
    return new_content


def postprocess_cross_references(content: str, heading_map: dict[str, str]) -> str:
    """Add internal links to cross-references like 'Section 3.2.1' in body text.

    Transforms:
      'See Section 3.2.1 for details' → 'See [Section 3.2.1](#321-overview) for details'

    Only links references that have a matching heading. Does not modify text
    inside headings, image alt text, or existing links.
    """
    if not heading_map:
        return content

    # Pattern: "Section N.N.N" not already inside a link [...]
    # Also handle "Table N" and "Figure N" references
    def _replace_ref(m: re.Match) -> str:
        full_match = m.group(0)
        kind = m.group(1)       # "Section", "Table", or "Figure"
        number = m.group(2)     # "3.2.1", "5", etc.

        # Build the lookup key
        if kind.lower() == "section":
            key = number
        else:
            key = f"{kind} {number}"

        anchor = heading_map.get(key, "")
        if not anchor:
            return full_match  # no matching heading, leave as-is

        return f"[{full_match}](#{anchor})"

    # Process line by line to skip headings, links, and image refs
    lines = content.split("\n")
    new_lines: list[str] = []

    for line in lines:
        # Skip headings, existing links, image refs, and reference definitions
        if (line.startswith("#")
            or line.startswith("[fig")
            or line.startswith("[img")
            or line.startswith("<!--")):
            new_lines.append(line)
            continue

        # Don't add links inside table header/separator rows
        if re.match(r"^\|[-\s|]+\|$", line):
            new_lines.append(line)
            continue

        # Replace references, but not ones already inside [...](...)
        # Use negative lookbehind for [ to avoid double-linking
        new_line = re.sub(
            r"(?<!\[)(Section|Table|Figure)\s+(\d+(?:\.\d+)*)",
            _replace_ref,
            line,
        )
        new_lines.append(new_line)

    return "\n".join(new_lines)


@dataclass
class _ImageInfo:
    """Metadata for a single image extracted during post-processing."""
    old_ref: str            # original path from docling markdown
    match_start: int        # character offset of ![Image](...) in content
    match_end: int          # end offset
    doc_figure_num: int = 0 # figure number from the document (0 = not a figure)
    caption: str = ""       # caption text from the document
    new_filename: str = ""  # final filename in figures/
    ref_id: str = ""        # markdown reference id


# Pattern for "Figure N. Caption" or "Figure N: Caption" or "## Figure N. Caption"
# Requires a period, colon, or dash after the number to distinguish captions
# from narrative references like "Figure 4 illustrates how..."
_FIGURE_CAPTION_RE = re.compile(
    r"^(?:#+ +)?Figure\s+(\d+)[\.:\-–—]\s*(.*)",
    re.IGNORECASE,
)


def _find_figure_caption(content: str, match_start: int, match_end: int) -> tuple[int, str]:
    """Search nearby lines for a 'Figure N. Caption' pattern.

    Looks both BEFORE (up to 5 non-empty lines) and AFTER (up to 5 non-empty
    lines) the image reference. This handles both caption-above and
    caption-below placements that docling produces.

    Returns (figure_number, caption_text) or (0, "") if no caption found.
    """
    # --- Look BEFORE the image ---
    before_text = content[max(0, match_start - 500):match_start]
    before_lines = before_text.split("\n")
    non_empty_seen = 0
    for line in reversed(before_lines):
        stripped = line.strip()
        if not stripped:
            continue
        non_empty_seen += 1
        if non_empty_seen > 5:
            break
        m = _FIGURE_CAPTION_RE.match(stripped)
        if m:
            return int(m.group(1)), m.group(2).strip().rstrip(".")

    # --- Look AFTER the image ---
    after_text = content[match_end:match_end + 500]
    after_lines = after_text.split("\n")
    non_empty_seen = 0
    for line in after_lines:
        stripped = line.strip()
        if not stripped:
            continue
        non_empty_seen += 1
        if non_empty_seen > 5:
            break
        m = _FIGURE_CAPTION_RE.match(stripped)
        if m:
            return int(m.group(1)), m.group(2).strip().rstrip(".")
        # Stop if we hit structural markdown (next section, table, another image)
        if stripped.startswith(("| ", "---", "![Image]")):
            break

    return 0, ""


def postprocess_output(output_dir: Path) -> None:
    """Reorganize converter output: rename images, flatten structure, update markdown.

    Transforms:
      - Nested artifacts dirs → flat figures/ directory
      - Maps each image to its document figure number by scanning for nearby
        "Figure N. Caption" lines (both above and below the image)
      - Numbered figures: figure_001.png matching "Figure 1" in the document
      - Non-figure images (logos, decorative): image_001.png, image_002.png
      - Reference-style markdown links with captions as alt text
      - Reference definitions collected at end of document
      - Removes empty leftover directories

    Output format:
      ![Figure 1: Conceptual Diagram of Accelerator][fig001]
      ![Logo][img001]
      ...
      [fig001]: figures/figure_001.png "Figure 1"
      [img001]: figures/image_001.png "Image"
    """
    md_files = list(output_dir.rglob("*.md"))
    if not md_files:
        log.warning(f"No markdown files found in {output_dir}, skipping post-processing")
        return

    md_file = md_files[0]
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(exist_ok=True)

    try:
        content = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.error(f"Failed to read {md_file}: {e}")
        return

    _POSTPROCESSED_MARKER = "<!-- pdf2md: post-processed -->"

    # Check if already post-processed
    if _POSTPROCESSED_MARKER in content:
        log.info(f"{md_file.name} already post-processed, running link validation only")
        _validate_internal_links(content, md_file.name)
        return

    # --- Step 1: Fix heading hierarchy (## → ##/###/####/##### by depth) ---
    content = postprocess_heading_levels(content)

    # --- Step 2: Build heading map for internal links ---
    heading_map = _build_heading_map(content)
    log.info(f"Built heading map with {len(heading_map)} entries")

    # --- Step 3: Clean up Table of Contents ---
    content = postprocess_toc(content, heading_map)

    # --- Step 4: Add cross-reference links in body text ---
    content = postprocess_cross_references(content, heading_map)

    # --- Step 5: Reorganize images ---
    # Find all image references in order of appearance.
    # Docling uses: ![Image](path/to/image_NNNNNN_<hash>.png)
    image_pattern = re.compile(r"!\[Image\]\(([^)]+)\)")
    matches = list(image_pattern.finditer(content))

    if not matches:
        log.info("No image references found in markdown, skipping image reorganization")
        # Still write TOC/cross-ref/heading changes even if no images
        content = _POSTPROCESSED_MARKER + "\n" + content
        try:
            md_file.write_text(content, encoding="utf-8")
        except OSError:
            pass
        _validate_internal_links(content, md_file.name)
        _cleanup_empty_dirs(output_dir, figures_dir)
        return

    log.info(f"Post-processing {len(matches)} image(s) in {md_file.name}")

    # Phase 1: Identify each image — match to document figure numbers
    images: list[_ImageInfo] = []
    seen_refs: set[str] = set()

    for match in matches:
        old_ref = match.group(1)
        if old_ref in seen_refs:
            continue
        seen_refs.add(old_ref)

        info = _ImageInfo(
            old_ref=old_ref,
            match_start=match.start(),
            match_end=match.end(),
        )
        fig_num, caption = _find_figure_caption(content, match.start(), match.end())
        info.doc_figure_num = fig_num
        info.caption = caption
        images.append(info)

    # Phase 2: Assign filenames and reference IDs
    # - Images matched to "Figure N" get figure_NNN.png (using the document's number)
    # - Unmatched images get image_NNN.png with a sequential counter
    non_figure_counter = 0
    used_figure_nums: set[int] = set()

    for info in images:
        ext = Path(info.old_ref).suffix or ".png"

        if info.doc_figure_num > 0 and info.doc_figure_num not in used_figure_nums:
            used_figure_nums.add(info.doc_figure_num)
            info.new_filename = f"figure_{info.doc_figure_num:03d}{ext}"
            info.ref_id = f"fig{info.doc_figure_num:03d}"
        else:
            non_figure_counter += 1
            info.new_filename = f"image_{non_figure_counter:03d}{ext}"
            info.ref_id = f"img{non_figure_counter:03d}"

    # Phase 3: Copy image files to figures/ with new names
    ref_map: dict[str, _ImageInfo] = {}
    for info in images:
        ref_map[info.old_ref] = info
        old_path = (md_file.parent / info.old_ref).resolve()
        new_path = figures_dir / info.new_filename

        if old_path.exists():
            try:
                shutil.copy2(old_path, new_path)
                log.debug(f"  {old_path.name} → {info.new_filename}")
            except OSError as e:
                log.error(f"Failed to copy {old_path} → {new_path}: {e}")
        else:
            log.warning(f"Referenced image not found: {old_path}")

    # Phase 4: Build reference definitions
    ref_definitions: list[str] = []
    for info in sorted(images, key=lambda i: (i.doc_figure_num == 0, i.doc_figure_num, i.new_filename)):
        if info.doc_figure_num > 0:
            title = f"Figure {info.doc_figure_num}"
        else:
            title = "Image"
        ref_definitions.append(f'[{info.ref_id}]: figures/{info.new_filename} "{title}"')

    # Phase 5: Replace inline references with reference-style links
    def _replace_image_ref(match_obj: re.Match) -> str:
        old_ref = match_obj.group(1)
        info = ref_map.get(old_ref)
        if info is None:
            return match_obj.group(0)

        if info.doc_figure_num > 0:
            if info.caption:
                alt_text = f"Figure {info.doc_figure_num}: {info.caption}"
            else:
                alt_text = f"Figure {info.doc_figure_num}: description pending"
        else:
            # Non-figure image — use nearby context as alt text
            before = content[max(0, info.match_start - 100):info.match_start]
            last_line = ""
            for line in reversed(before.split("\n")):
                stripped = line.strip()
                if stripped:
                    last_line = stripped
                    break
            alt_text = last_line if last_line else "Image"

        return f"![{alt_text}][{info.ref_id}]"

    new_content = image_pattern.sub(_replace_image_ref, content)

    # Append reference definitions at the end of the document
    new_content = new_content.rstrip() + "\n\n"
    new_content += "<!-- Figure reference definitions -->\n"
    new_content += "\n".join(ref_definitions) + "\n"

    # Stats
    matched = sum(1 for i in images if i.doc_figure_num > 0)
    unmatched = len(images) - matched

    # Write updated markdown with post-processing marker
    new_content = _POSTPROCESSED_MARKER + "\n" + new_content
    try:
        md_file.write_text(new_content, encoding="utf-8")
        log.info(
            f"Updated {md_file.name}: {matched} figures matched to document numbers, "
            f"{unmatched} non-figure image(s)"
        )
    except OSError as e:
        log.error(f"Failed to write updated markdown: {e}")
        return

    # Remove old artifacts directories
    artifacts_dirs = list(output_dir.rglob("*_artifacts"))
    for artifacts_dir in artifacts_dirs:
        try:
            shutil.rmtree(artifacts_dir)
            log.debug(f"Removed old artifacts dir: {artifacts_dir}")
        except OSError as e:
            log.warning(f"Failed to remove {artifacts_dir}: {e}")

    _cleanup_empty_dirs(output_dir, figures_dir)

    # --- Step 6: Validate internal links ---
    try:
        final_content = md_file.read_text(encoding="utf-8", errors="replace")
        _validate_internal_links(final_content, md_file.name)
    except OSError:
        pass


def _validate_internal_links(content: str, filename: str) -> None:
    """Check all internal markdown links resolve to a valid heading anchor.

    Reports a summary of working and broken links. For each broken link,
    shows the anchor, link text, and line number to help the user fix it.
    """
    # Build anchor set from all headings (GitHub-flavored markdown rules)
    anchors: set[str] = set()
    for line in content.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            text = m.group(2).strip().lower()
            text = re.sub(r"[^\w\s-]", "", text)
            text = re.sub(r"\s+", "-", text.strip())
            text = re.sub(r"-+", "-", text)
            anchors.add(text)

    # Find all internal links: [text](#anchor)
    link_pattern = re.compile(r"\[([^\]]*)\]\(#([^)]+)\)")
    lines = content.splitlines()

    total = 0
    broken: list[tuple[int, str, str]] = []  # (line_number, anchor, link_text)

    for line_num, line in enumerate(lines, 1):
        for m in link_pattern.finditer(line):
            total += 1
            link_text = m.group(1)
            anchor = m.group(2)
            if anchor not in anchors:
                broken.append((line_num, anchor, link_text[:80]))

    working = total - len(broken)

    if broken:
        log.warning(
            f"Link check ({filename}): {working} working, "
            f"{len(broken)} broken out of {total} internal links"
        )
        for line_num, anchor, link_text in broken:
            log.warning(f"  Line {line_num}: #{anchor} <- \"{link_text}\"")
    else:
        log.info(
            f"Link check ({filename}): {total} internal links, all valid"
        )


def _cleanup_empty_dirs(output_dir: Path, keep: Path) -> None:
    """Remove empty directories left behind after reorganization."""
    for dirpath in sorted(output_dir.rglob("*"), reverse=True):
        if not dirpath.is_dir():
            continue
        if dirpath == keep or dirpath == output_dir:
            continue
        try:
            if not any(dirpath.iterdir()):
                dirpath.rmdir()
                log.debug(f"Removed empty dir: {dirpath}")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Abstract converter base class
# ---------------------------------------------------------------------------


class BaseConverter(abc.ABC):
    """Base class for PDF-to-Markdown converters.

    To add a new converter (e.g., llamaparse, markitdown):
      1. Create a subclass of BaseConverter
      2. Implement convert_file() and name property
      3. Register it in CONVERTERS dict at the bottom of this file
    """

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.num_threads = args.num_threads or detect_num_threads()

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short identifier for this converter."""
        ...

    @abc.abstractmethod
    def convert_file(self, pdf_path: Path, output_dir: Path) -> ConversionStats:
        """Convert a single PDF file. Returns stats about the conversion."""
        ...

    def check_dependencies(self) -> bool:
        """Verify that required tools/packages are available. Returns True if OK."""
        return True


# ---------------------------------------------------------------------------
# Docling converter
# ---------------------------------------------------------------------------


class DoclingConverter(BaseConverter):
    """Converter using IBM Docling (standard pipeline)."""

    SUPPORTED_OCR_ENGINES = ("rapidocr", "easyocr", "tesserocr", "tesseract", "auto")

    @property
    def name(self) -> str:
        return "docling"

    def __init__(self, args: argparse.Namespace):
        super().__init__(args)
        self._wrapper_path: Optional[Path] = None

    def _cleanup_wrapper(self) -> None:
        """Remove the temporary wrapper script."""
        if self._wrapper_path and self._wrapper_path.exists():
            try:
                self._wrapper_path.unlink()
            except OSError:
                pass
            self._wrapper_path = None

    def check_dependencies(self) -> bool:
        try:
            result = subprocess.run(
                ["docling", "--version"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
                log.info(f"Docling version: {version}")
                return True
        except FileNotFoundError:
            log.error("'docling' CLI not found. Install with: uv pip install docling")
        except subprocess.TimeoutExpired:
            log.error("'docling --version' timed out")
        return False

    def _build_command(
        self, pdf_path: Path, output_dir: Path, params: _TuningParams,
    ) -> list[str]:
        """Build the docling CLI command.

        Wraps docling in a Python script that sets torch thread limits
        BEFORE any ML library is imported. This is the only reliable way to
        limit PyTorch inter-op threads, which ignore environment variables.
        """
        ocr_engine = getattr(self.args, "ocr_engine", "rapidocr")
        timeout = getattr(self.args, "timeout", None)

        docling_args = [
            "--pipeline", "standard",
            "--to", "md",
            "--ocr",
            "--ocr-engine", ocr_engine,
            "--tables",
            "--table-mode", "accurate",
            "--image-export-mode", "referenced",
            "--pdf-backend", "docling_parse",
            "--device", "cpu",
            "--num-threads", str(params.threads),
            "--page-batch-size", str(params.batch_size),
            "--abort-on-error",
            "-v",
            "--output", str(output_dir),
        ]

        if params.enable_formula:
            docling_args.append("--enrich-formula")
        if params.enable_code:
            docling_args.append("--enrich-code")
        if params.enable_picture_classes:
            docling_args.append("--enrich-picture-classes")

        if timeout:
            docling_args.extend(["--document-timeout", str(timeout)])

        docling_args.append(str(pdf_path))

        # Build a wrapper script that constrains torch threads before import.
        # Environment variables handle OMP/MKL/ONNX, but PyTorch inter-op
        # threads can only be set via the API before first model use.
        omp = params.omp_threads
        wrapper = (
            f"import sys; sys.argv = sys.argv[:1] + {docling_args!r}\n"
            f"import torch; "
            f"torch.set_num_threads({omp}); "
            f"torch.set_num_interop_threads(1)\n"
            f"from docling.cli.main import app; app()\n"
        )
        # Write wrapper to a temp file (cleaned up by the OS)
        import tempfile
        wrapper_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="pdf2md_docling_",
            delete=False, dir=str(output_dir),
        )
        wrapper_file.write(wrapper)
        wrapper_file.close()
        self._wrapper_path = Path(wrapper_file.name)

        cmd = [sys.executable, wrapper_file.name]
        return cmd

    @dataclass
    class _TuningParams:
        threads: int = 4
        batch_size: int = 4
        enable_formula: bool = True
        enable_code: bool = True
        enable_picture_classes: bool = True
        omp_threads: int = 4

    def _tune_for_document(self, pdf_path: Path) -> _TuningParams:
        """Auto-tune all parameters based on document size and available RAM.

        On memory-constrained systems, progressively disables enrichment
        models (CodeFormula VLM first, then picture classifier) and reduces
        internal ML thread counts to stay within memory budget.

        Approximate model memory footprint:
          Layout detection:    ~500 MB
          TableFormer:         ~500 MB
          OCR engine:          ~300 MB
          Picture classifier:  ~200 MB
          CodeFormula VLM:     ~1000 MB  (biggest single model)
          PyTorch runtime:     ~200 MB per OMP thread in buffers
        """
        page_count = get_pdf_page_count(pdf_path)
        mem_gb = get_available_memory_gb()
        params = self._TuningParams()

        # --- Threads ---
        if self.args.num_threads:
            params.threads = self.args.num_threads
        else:
            params.threads = compute_safe_threads(page_count, mem_gb)

        # --- Batch size ---
        if mem_gb < 8 or page_count > 500:
            params.batch_size = 1
        elif mem_gb < 16 or page_count > 200:
            params.batch_size = 2
        else:
            params.batch_size = 4

        # --- OMP/PyTorch internal threads ---
        # Each ML model uses OMP threads for inference. On memory-constrained
        # systems, limiting this reduces per-model buffer allocation.
        if mem_gb < 10:
            params.omp_threads = 1
        elif mem_gb < 16:
            params.omp_threads = 2
        else:
            params.omp_threads = max(1, (os.cpu_count() or 4) // 2)

        # --- Enrichment models ---
        # Progressively disable expensive models when memory is tight.
        # CodeFormula VLM (~1 GB) is the first to go.
        if mem_gb < 12:
            params.enable_formula = False
            params.enable_code = False
            log.info("Disabled formula/code enrichment to save ~1 GB RAM")
        if mem_gb < 8:
            params.enable_picture_classes = False
            log.info("Disabled picture classification to save ~200 MB RAM")

        log.info(
            f"Document: {page_count} pages, {mem_gb:.1f} GB available → "
            f"threads={params.threads}, batch={params.batch_size}, "
            f"omp={params.omp_threads}, "
            f"formula={'on' if params.enable_formula else 'off'}, "
            f"pictures={'on' if params.enable_picture_classes else 'off'}"
        )
        return params

    def convert_file(self, pdf_path: Path, output_dir: Path) -> ConversionStats:
        stats = ConversionStats(
            input_file=str(pdf_path),
            input_size_bytes=pdf_path.stat().st_size,
            converter=self.name,
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        params = self._tune_for_document(pdf_path)
        cmd = self._build_command(pdf_path, output_dir, params)

        page_count = get_pdf_page_count(pdf_path)
        enrichments = []
        if params.enable_formula:
            enrichments.append("formula")
        if params.enable_code:
            enrichments.append("code")
        if params.enable_picture_classes:
            enrichments.append("pictures")
        enrich_str = ", ".join(enrichments) if enrichments else "none"
        print(
            f"  Pages:  {page_count} | Threads: {params.threads} | "
            f"Batch: {params.batch_size} | OMP: {params.omp_threads} | "
            f"Enrichments: {enrich_str}"
        )

        log.info(f"Running: {' '.join(cmd[:6])}... → {output_dir}")
        log.debug(f"Full command: {' '.join(cmd)}")

        env = os.environ.copy()
        env["TORCH_COMPILE_DISABLE"] = "1"
        # --- Limit ALL ML runtime threads and worker processes ---
        # Without these, each library spawns its own pool of threads/processes,
        # causing OOM even with --num-threads 1.
        t = str(params.omp_threads)
        # OpenMP (used by PyTorch, ONNX, NumPy)
        env["OMP_NUM_THREADS"] = t
        # Intel MKL (used by PyTorch on Intel CPUs)
        env["MKL_NUM_THREADS"] = t
        # OpenBLAS (alternative BLAS backend)
        env["OPENBLAS_NUM_THREADS"] = t
        # ONNX Runtime (used by RapidOCR)
        env["ORT_NUM_THREADS"] = t
        # NumPy/SciPy thread control
        env["NUMEXPR_MAX_THREADS"] = t
        # HuggingFace tokenizers spawn Rust threads
        env["TOKENIZERS_PARALLELISM"] = "false"
        # PyTorch DataLoader workers (0 = main process only)
        env["DATALOADER_NUM_WORKERS"] = "0"
        # PyTorch inter-op parallelism
        env["TORCH_NUM_THREADS"] = t
        env["TORCH_NUM_INTEROP_THREADS"] = "1"

        progress = ProgressLine()
        stderr_lines: list[str] = []

        def _kill_process_tree(proc: subprocess.Popen) -> None:
            """Kill the subprocess and all its children via process group."""
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
                    proc.wait(timeout=5)
            except (OSError, ProcessLookupError):
                # Process already exited
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass

        start = time.monotonic()
        try:
            # Start subprocess in its own process group so we can kill
            # the entire tree (docling + PyTorch workers + OCR) on interrupt.
            global _active_proc
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            _active_proc = proc
            progress.start()

            # Stream stderr line by line for progress updates
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line)
                progress.update_from_line(line)
                if _shutdown_requested:
                    break

            proc.wait(timeout=self.args.timeout if self.args.timeout else None)
            stats.elapsed_seconds = time.monotonic() - start
            progress.stop()
            _active_proc = None

            # If shutdown was requested via signal handler, treat as interrupt
            if _shutdown_requested:
                stats.error = "Interrupted by user"
                raise KeyboardInterrupt

            if proc.returncode != 0:
                stderr_text = "".join(stderr_lines)
                if proc.returncode == -9 or proc.returncode == 137:
                    stats.error = (
                        f"Killed by system (likely out of memory). "
                        f"Try --num-threads 2 or a smaller document."
                    )
                else:
                    stats.error = _extract_error(stderr_text)
                log.error(f"Docling failed for {pdf_path.name}: {stats.error}")
                return stats

        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            _active_proc = None
            stats.elapsed_seconds = time.monotonic() - start
            progress.stop()
            self._cleanup_wrapper()
            stats.error = f"Timed out after {self.args.timeout}s"
            log.error(f"Timeout converting {pdf_path.name}")
            return stats
        except KeyboardInterrupt:
            _kill_process_tree(proc)
            _active_proc = None
            stats.elapsed_seconds = time.monotonic() - start
            progress.stop()
            self._cleanup_wrapper()
            stats.error = "Interrupted by user"
            raise

        self._cleanup_wrapper()

        # Post-process: reorganize images, clean up paths
        try:
            postprocess_output(output_dir)
        except Exception as e:
            log.warning(f"Post-processing failed (conversion still valid): {e}")

        # Compute stats from the final (post-processed) output
        md_files = list(output_dir.rglob("*.md"))
        if md_files:
            md_file = md_files[0]
            stats.output_file = str(md_file)
            figures_dir = output_dir / "figures"

            md_stats = compute_markdown_stats(md_file, figures_dir)
            stats.output_size_bytes = md_stats["output_size_bytes"]
            stats.output_lines = md_stats["output_lines"]
            stats.num_headings = md_stats["num_headings"]
            stats.num_table_rows = md_stats["num_table_rows"]
            stats.num_images = md_stats["num_images"]
            stats.num_image_files = md_stats["num_image_files"]
            stats.success = True
        else:
            stats.error = "No markdown output file found"
            log.error(f"No .md file produced for {pdf_path.name}")

        return stats


def _extract_error(stderr: str) -> str:
    """Extract the most useful error message from stderr."""
    lines = stderr.strip().splitlines()
    for line in reversed(lines):
        if "Error:" in line or "Exception:" in line:
            return line.strip()
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return "Unknown error (no stderr output)"


# ---------------------------------------------------------------------------
# Converter registry — add new converters here
# ---------------------------------------------------------------------------

CONVERTERS: dict[str, type[BaseConverter]] = {
    "docling": DoclingConverter,
    # Future converters:
    # "llamaparse": LlamaParseConverter,
    # "markitdown": MarkItDownConverter,
}


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------


def process_files(
    pdf_files: list[Path],
    converter: BaseConverter,
    output_dir: Optional[Path],
    force: bool,
) -> RunSummary:
    """Process a list of PDF files and return a summary."""
    global _shutdown_requested

    summary = RunSummary(total_files=len(pdf_files))
    total_start = time.monotonic()

    for i, pdf_path in enumerate(pdf_files, 1):
        if _shutdown_requested:
            log.warning(
                f"Skipping remaining {len(pdf_files) - i + 1} file(s) due to interrupt"
            )
            summary.skipped += len(pdf_files) - i + 1
            break

        file_output_dir = compute_output_dir(pdf_path, output_dir)
        md_exists = (
            any(file_output_dir.rglob("*.md")) if file_output_dir.exists() else False
        )

        print(f"\n{'='*70}")
        print(f"[{i}/{len(pdf_files)}] {pdf_path.name}")
        print(f"  Input:  {pdf_path} ({format_size(pdf_path.stat().st_size)})")
        print(f"  Output: {file_output_dir}/")

        if md_exists and not force:
            print(f"  Status: SKIPPED (output exists, use --force to re-process)")
            stats = ConversionStats(
                input_file=str(pdf_path),
                input_size_bytes=pdf_path.stat().st_size,
                skipped=True,
                converter=converter.name,
            )
            summary.skipped += 1
            summary.file_stats.append(stats)
            continue

        try:
            stats = converter.convert_file(pdf_path, file_output_dir)
        except KeyboardInterrupt:
            _shutdown_requested = True
            stats = ConversionStats(
                input_file=str(pdf_path),
                input_size_bytes=pdf_path.stat().st_size,
                error="Interrupted by user",
                converter=converter.name,
            )
            summary.failed += 1
            summary.file_stats.append(stats)
            log.warning("Conversion interrupted")
            break

        summary.file_stats.append(stats)

        if stats.success:
            summary.successful += 1
            print(f"  Status: OK ({format_duration(stats.elapsed_seconds)})")
            print(
                f"  Output: {format_size(stats.output_size_bytes)}, "
                f"{stats.output_lines} lines, "
                f"{stats.num_headings} headings, "
                f"{stats.num_table_rows} table rows, "
                f"{stats.num_images} figures, "
                f"{stats.num_image_files} image files"
            )
        else:
            summary.failed += 1
            print(f"  Status: FAILED — {stats.error}")

    summary.total_elapsed_seconds = time.monotonic() - total_start
    return summary


def print_summary(summary: RunSummary) -> None:
    """Print a final summary table."""
    print(f"\n{'='*70}")
    print("CONVERSION SUMMARY")
    print(f"{'='*70}")
    print(f"  Total files:   {summary.total_files}")
    print(f"  Successful:    {summary.successful}")
    print(f"  Failed:        {summary.failed}")
    print(f"  Skipped:       {summary.skipped}")
    print(f"  Total time:    {format_duration(summary.total_elapsed_seconds)}")

    if summary.successful > 0:
        successful_stats = [s for s in summary.file_stats if s.success]
        total_input = sum(s.input_size_bytes for s in successful_stats)
        total_output = sum(s.output_size_bytes for s in successful_stats)
        total_lines = sum(s.output_lines for s in successful_stats)
        total_headings = sum(s.num_headings for s in successful_stats)
        total_tables = sum(s.num_table_rows for s in successful_stats)
        total_images = sum(s.num_image_files for s in successful_stats)
        total_time = sum(s.elapsed_seconds for s in successful_stats)

        print(f"\n  Totals (successful conversions):")
        print(f"    Input size:    {format_size(total_input)}")
        print(f"    Output size:   {format_size(total_output)}")
        print(f"    Lines:         {total_lines:,}")
        print(f"    Headings:      {total_headings:,}")
        print(f"    Table rows:    {total_tables:,}")
        print(f"    Image files:   {total_images:,}")
        print(f"    Processing:    {format_duration(total_time)}")
        if total_time > 0:
            print(
                f"    Throughput:    ~{format_size(int(total_input / total_time))}/s"
            )

    if summary.failed > 0:
        print(f"\n  Failed files:")
        for s in summary.file_stats:
            if not s.success and not s.skipped:
                print(f"    - {s.input_file}: {s.error}")

    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="Convert PDF documents to Markdown using pluggable converter backends.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s raw/document.pdf                     # single file
  %(prog)s raw/doc1.pdf raw/doc2.pdf             # multiple files
  %(prog)s raw/                                  # entire directory
  %(prog)s raw/ --output-dir processed/          # custom output dir
  %(prog)s raw/ --ocr-engine easyocr             # use EasyOCR
  %(prog)s raw/ --force                          # re-process existing
  %(prog)s raw/ --converter docling              # explicit converter
  %(prog)s raw/ --save-stats stats.json          # save stats to JSON
  %(prog)s --postprocess processed/CXL_1.1/      # post-process one dir
  %(prog)s --postprocess processed/CXL_*/        # post-process multiple
  %(prog)s raw/ -o processed/ --postprocess       # post-process all output dirs
        """,
    )

    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="FILE_OR_DIR",
        help="One or more PDF files or directories containing PDFs",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Base output directory (default: <input_dir>/processed/<file_stem>/)",
    )
    parser.add_argument(
        "-c", "--converter",
        choices=list(CONVERTERS.keys()),
        default="docling",
        help="Converter backend to use (default: docling)",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=DoclingConverter.SUPPORTED_OCR_ENGINES,
        default="rapidocr",
        help="OCR engine for docling (default: rapidocr)",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=None,
        help="Number of threads (default: auto-detect CPUs - 1, minimum 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout per document in seconds (default: no timeout)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process files even if output already exists",
    )
    parser.add_argument(
        "--postprocess",
        nargs="*",
        metavar="DIR",
        help="Run post-processing only (no PDF conversion) on existing output "
             "directories. Pass directory paths, or omit paths to auto-detect "
             "from --output-dir.",
    )
    parser.add_argument(
        "--save-stats",
        type=Path,
        default=None,
        metavar="FILE",
        help="Save conversion statistics to a JSON file",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt for large batches",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for info, -vv for debug)",
    )

    return parser


def _collect_postprocess_dirs(args: argparse.Namespace) -> list[Path]:
    """Collect directories to post-process.

    If --postprocess is given with paths, use those.
    If --postprocess is given without paths, find all subdirs under --output-dir
    or under <input>/processed/ that contain .md files.
    """
    dirs: list[Path] = []

    if args.postprocess:
        # Explicit paths provided
        for p in args.postprocess:
            path = Path(p).resolve()
            if path.is_dir():
                # If this dir has .md files, use it directly
                if list(path.rglob("*.md")):
                    dirs.append(path)
                else:
                    # Maybe it's a parent dir — check subdirs
                    for child in sorted(path.iterdir()):
                        if child.is_dir() and list(child.rglob("*.md")):
                            dirs.append(child)
            else:
                log.warning(f"Not a directory: {p}")
    else:
        # No paths — auto-detect from --output-dir or inputs
        search_roots: list[Path] = []
        if args.output_dir:
            search_roots.append(Path(args.output_dir).resolve())
        for inp in args.inputs:
            p = Path(inp).resolve()
            if p.is_dir():
                processed = p / "processed"
                if processed.is_dir():
                    search_roots.append(processed)

        for root in search_roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if child.is_dir() and list(child.rglob("*.md")):
                    dirs.append(child)

    return dirs


def _run_postprocess_only(args: argparse.Namespace) -> int:
    """Run post-processing on existing markdown output directories."""
    dirs = _collect_postprocess_dirs(args)

    if not dirs:
        print("No directories with markdown files found for post-processing.")
        if not args.postprocess:
            print("  Specify directories: --postprocess dir1/ dir2/")
            print("  Or use with -o: --postprocess -o processed/")
        return 1

    print(f"\nPost-processing {len(dirs)} directory(ies):")
    for d in dirs:
        print(f"  - {d}/")
    print()

    success = 0
    failed = 0
    for i, d in enumerate(dirs, 1):
        print(f"[{i}/{len(dirs)}] {d.name}/")
        try:
            postprocess_output(d)
            success += 1
            # Print stats from the output
            md_files = list(d.rglob("*.md"))
            if md_files:
                figures_dir = d / "figures"
                stats = compute_markdown_stats(md_files[0], figures_dir)
                print(
                    f"  Done: {stats['output_lines']} lines, "
                    f"{stats['num_headings']} headings, "
                    f"{stats['num_table_rows']} table rows, "
                    f"{stats['num_images']} figures, "
                    f"{stats['num_image_files']} image files"
                )
        except Exception as e:
            log.error(f"Post-processing failed for {d}: {e}")
            failed += 1

    print(f"\nPost-processing complete: {success} succeeded, {failed} failed")
    return 1 if failed > 0 else 0


def main() -> int:
    signal.signal(signal.SIGINT, _signal_handler)

    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Post-process-only mode ---
    if args.postprocess is not None:
        return _run_postprocess_only(args)

    # Normal mode requires input files
    if not args.inputs:
        parser.error("the following arguments are required: FILE_OR_DIR")

    # Validate thread count if explicitly set
    if args.num_threads is not None:
        if args.num_threads < 1:
            log.warning(f"Thread count {args.num_threads} is invalid, using 1")
            args.num_threads = 1

    # Collect PDF files
    pdf_files = collect_pdf_files(args.inputs)
    if not pdf_files:
        print("No PDF files found in the specified inputs.")
        print("  Only .pdf files are supported. Directories are scanned recursively.")
        return 1

    total_size = sum(f.stat().st_size for f in pdf_files)
    print(f"\nFound {len(pdf_files)} PDF file(s) ({format_size(total_size)} total):")
    for f in pdf_files:
        print(f"  - {f.name} ({format_size(f.stat().st_size)})")

    # Confirm large batches
    if len(pdf_files) > CONFIRM_THRESHOLD and not args.yes:
        if not confirm_large_batch(pdf_files):
            print("Aborted.")
            return 0

    # Initialize converter
    converter_cls = CONVERTERS[args.converter]
    converter = converter_cls(args)

    if not converter.check_dependencies():
        print(
            f"Dependency check failed for converter '{args.converter}'. Aborting."
        )
        return 1

    threads = args.num_threads or detect_num_threads()
    print(f"\nConverter: {converter.name} | OCR: {args.ocr_engine} | Threads: {threads}")

    # Process
    summary = process_files(pdf_files, converter, args.output_dir, args.force)
    print_summary(summary)

    # Save stats if requested
    if args.save_stats:
        try:
            stats_data = {
                "total_files": summary.total_files,
                "successful": summary.successful,
                "failed": summary.failed,
                "skipped": summary.skipped,
                "total_elapsed_seconds": summary.total_elapsed_seconds,
                "files": [asdict(s) for s in summary.file_stats],
            }
            args.save_stats.parent.mkdir(parents=True, exist_ok=True)
            args.save_stats.write_text(json.dumps(stats_data, indent=2))
            print(f"Stats saved to: {args.save_stats}")
        except OSError as e:
            log.error(f"Failed to save stats: {e}")

    return 1 if summary.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
