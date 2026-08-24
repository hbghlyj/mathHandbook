#!/usr/bin/env python3
"""Replace legacy Symbol-font <span> contents with Unicode characters.

Word HTML encodes Greek letters and math operators as Symbol-font bytes
wrapped in ``<span style='font-family:Symbol'>``.  This script:

* downloads (or reads a cached copy of) the Adobe Symbol→Unicode mapping
* maps only text nodes / HTML entities inside those spans
* never mutates nested markup such as ``<img>``, ``<sub>``, ``<i>``
* removes only the ``font-family: Symbol`` CSS declaration, leaving
  properties like ``font-style`` or ``letter-spacing`` intact
* drops an empty ``style`` attribute, and unwraps the ``<span>`` entirely
  when no attributes remain
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

MAPPING_URLS = (
    "https://unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt",
    "https://www.unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt",
)

# Corporate-Use PUA code points from the Adobe table → standard Unicode.
# These are the constructed-glyph pieces (large parens, extenders, etc.).
PUA_TO_UNICODE = {
    0xF8E5: 0x203E,  # radical extender → OVERLINE
    0xF8E6: 0x23D0,  # vertical arrow extender
    0xF8E7: 0x23AF,  # horizontal arrow extender
    0xF6DA: 0x00AE,  # registered serif
    0xF6D9: 0x00A9,  # copyright serif
    0xF6DB: 0x2122,  # trademark serif
    0xF8E8: 0x00AE,  # registered sans
    0xF8E9: 0x00A9,  # copyright sans
    0xF8EA: 0x2122,  # trademark sans
    0xF8EB: 0x239B,  # left paren top
    0xF8EC: 0x239C,  # left paren extender
    0xF8ED: 0x239D,  # left paren bottom
    0xF8EE: 0x23A1,  # left square bracket top
    0xF8EF: 0x23A2,  # left square bracket extender
    0xF8F0: 0x23A3,  # left square bracket bottom
    0xF8F1: 0x23A7,  # left curly bracket top
    0xF8F2: 0x23A8,  # left curly bracket mid
    0xF8F3: 0x23A9,  # left curly bracket bottom
    0xF8F4: 0x23AA,  # curly bracket extender
    0xF8F5: 0x23AE,  # integral extender
    0xF8F6: 0x239E,  # right paren top
    0xF8F7: 0x239F,  # right paren extender
    0xF8F8: 0x23A0,  # right paren bottom
    0xF8F9: 0x23A4,  # right square bracket top
    0xF8FA: 0x23A5,  # right square bracket extender
    0xF8FB: 0x23A6,  # right square bracket bottom
    0xF8FC: 0x23AB,  # right curly bracket top
    0xF8FD: 0x23AC,  # right curly bracket mid
    0xF8FE: 0x23AD,  # right curly bracket bottom
}

SPAN_OPEN_RE = re.compile(r"<span\b", re.IGNORECASE)
CLOSE_SPAN_RE = re.compile(r"</span\s*>", re.IGNORECASE)
STYLE_ATTR_RE = re.compile(
    r"""(\s*)style\s*=\s*(?:'([^']*)'|"([^"]*)")""",
    re.IGNORECASE | re.DOTALL,
)
SYMBOL_FONT_RE = re.compile(r"font-family:\s*Symbol\b", re.IGNORECASE)
# Review requirement: strip only the Symbol family, keep other CSS.
SYMBOL_FAMILY_DECL_RE = re.compile(
    r"font-family:\s*Symbol\s*;?",
    re.IGNORECASE,
)
# Tokenize innerHTML: tags stay untouched; entities and text are mapped.
INNER_TOKEN_RE = re.compile(
    r"(<[^>]+>)"
    r"|(&(?:[A-Za-z][A-Za-z0-9]*|#\d+|#x[0-9A-Fa-f]+);)"
    r"|([^<&]+)"
    r"|.",
    re.DOTALL,
)
BARE_SPAN_RE = re.compile(r"<span\s*>", re.IGNORECASE)


def _score_unicode(cp: int) -> int:
    """Prefer Greek letters and math operators over PUA / compatibility chars."""
    score = 0
    if 0xE000 <= cp <= 0xF8FF or 0xF000 <= cp <= 0xF8FF:
        score -= 100
    if 0x0370 <= cp <= 0x03FF:
        score += 10
    if 0x2200 <= cp <= 0x22FF:
        score += 5
    return score


def parse_adobe_mapping(text: str) -> Dict[int, str]:
    """Parse the Adobe Symbol Encoding table into {symbol_byte: unicode_char}."""
    mapping: Dict[int, int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        try:
            uni_cp = int(parts[0], 16)
            sym_cp = int(parts[1], 16)
        except ValueError:
            continue
        if uni_cp in PUA_TO_UNICODE:
            uni_cp = PUA_TO_UNICODE[uni_cp]
        if sym_cp not in mapping or _score_unicode(uni_cp) > _score_unicode(
            mapping[sym_cp]
        ):
            mapping[sym_cp] = uni_cp
    return {byte: chr(cp) for byte, cp in mapping.items()}


def download_mapping(dest: Path, timeout: float = 20.0) -> Optional[str]:
    """Try official Unicode.org URLs; return file text on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[BaseException] = None
    for url in MAPPING_URLS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mathHandbook-symbol-replace/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            text = data.decode("utf-8", errors="replace")
            dest.write_text(text, encoding="utf-8")
            return text
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            continue
    if last_error is not None:
        print(f"warning: could not download mapping ({last_error})", file=sys.stderr)
    return None


def load_mapping(mapping_path: Path, download: bool = True) -> Dict[int, str]:
    text: Optional[str] = None
    if download:
        downloaded = download_mapping(mapping_path)
        if downloaded is not None:
            text = downloaded
    if text is None and mapping_path.is_file():
        text = mapping_path.read_text(encoding="utf-8")
    if text is None:
        raise FileNotFoundError(
            f"Symbol mapping not found at {mapping_path} and download failed"
        )
    mapping = parse_adobe_mapping(text)
    if not mapping:
        raise ValueError(f"no Symbol mappings parsed from {mapping_path}")
    return mapping


def find_tag_end(text: str, start: int) -> int:
    """Index of the '>' that closes the tag at *start*, respecting quotes."""
    quote: Optional[str] = None
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == ">":
                return i
        i += 1
    return -1


def find_matching_close_span(text: str, inner_start: int) -> Tuple[int, int]:
    """Return (close_start, close_end) for the ``</span>`` matching depth 1."""
    depth = 1
    i = inner_start
    n = len(text)
    while i < n:
        if text[i] != "<":
            i += 1
            continue
        close = CLOSE_SPAN_RE.match(text, i)
        if close:
            depth -= 1
            if depth == 0:
                return close.start(), close.end()
            i = close.end()
            continue
        if SPAN_OPEN_RE.match(text, i):
            gt = find_tag_end(text, i)
            if gt < 0:
                break
            depth += 1
            i = gt + 1
            continue
        i += 1
    return -1, -1


def map_char(ch: str, mapping: Dict[int, str]) -> str:
    """Map one Symbol-encoded character.  NBSP is never treated as Euro."""
    if ch == "\u00a0":
        return ch
    cp = ord(ch)
    if cp <= 0xFF and cp in mapping:
        return mapping[cp]
    return ch


def escape_text(s: str) -> str:
    """Re-escape text so mapped '<' / '>' / '&' cannot become markup."""
    out: List[str] = []
    for ch in s:
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == "\u00a0":
            out.append("&nbsp;")
        else:
            out.append(ch)
    return "".join(out)


def map_text_node(text: str, mapping: Dict[int, str]) -> str:
    return escape_text("".join(map_char(ch, mapping) for ch in text))


def map_inner_html(inner: str, mapping: Dict[int, str]) -> str:
    """Map Symbol text/entities; copy nested HTML tags through unchanged."""
    pieces: List[str] = []
    for match in INNER_TOKEN_RE.finditer(inner):
        token = match.group(0)
        if token.startswith("<") and token.endswith(">"):
            pieces.append(token)
            continue
        if token.startswith("&") and token.endswith(";"):
            pieces.append(map_text_node(html.unescape(token), mapping))
            continue
        pieces.append(map_text_node(token, mapping))
    return "".join(pieces)


def strip_symbol_from_style(style_content: str) -> str:
    """Remove only ``font-family: Symbol``, keep every other CSS property."""
    new = SYMBOL_FAMILY_DECL_RE.sub("", style_content)
    new = re.sub(r"^\s*;\s*", "", new)
    new = re.sub(r";\s*;+", ";", new)
    new = new.strip()
    new = new.strip(";")
    return new.strip()


def strip_symbol_style(open_tag: str) -> str:
    """Drop ``font-family:Symbol`` from the opening tag; maybe drop style/span."""

    def _repl(match: re.Match[str]) -> str:
        leading_ws = match.group(1)
        style_content = match.group(2) if match.group(2) is not None else match.group(3)
        quote = "'" if match.group(2) is not None else '"'
        remaining = strip_symbol_from_style(style_content)
        if not remaining:
            return ""
        return f"{leading_ws}style={quote}{remaining}{quote}"

    new_tag = STYLE_ATTR_RE.sub(_repl, open_tag, count=1)
    # Collapse a now-empty `<span   >` but keep original newlines/spacing
    # around any remaining attributes (e.g. lang=EN-US).
    new_tag = re.sub(r"<span\s+>", "<span>", new_tag, flags=re.IGNORECASE)
    return new_tag


def is_bare_span(open_tag: str) -> bool:
    return BARE_SPAN_RE.fullmatch(open_tag) is not None


def replace_symbol_spans(text: str, mapping: Dict[int, str]) -> Tuple[str, int]:
    """Rewrite every Symbol-font span in *text*.  Returns (new_text, count)."""
    out: List[str] = []
    pos = 0
    count = 0
    n = len(text)
    while pos < n:
        match = SPAN_OPEN_RE.search(text, pos)
        if not match:
            out.append(text[pos:])
            break
        span_start = match.start()
        out.append(text[pos:span_start])
        gt = find_tag_end(text, span_start)
        if gt < 0:
            out.append(text[span_start:])
            break
        open_tag = text[span_start : gt + 1]
        if not SYMBOL_FONT_RE.search(open_tag):
            out.append(open_tag)
            pos = gt + 1
            continue
        inner_start = gt + 1
        close_start, close_end = find_matching_close_span(text, inner_start)
        if close_start < 0:
            out.append(open_tag)
            pos = gt + 1
            continue
        inner = text[inner_start:close_start]
        new_inner = map_inner_html(inner, mapping)
        new_open = strip_symbol_style(open_tag)
        if is_bare_span(new_open):
            out.append(new_inner)
        else:
            out.append(new_open)
            out.append(new_inner)
            out.append(text[close_start:close_end])
        count += 1
        pos = close_end
    return "".join(out), count


def iter_markdown_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.parts):
            continue
        yield path


def process_file(path: Path, mapping: Dict[int, str], dry_run: bool = False) -> int:
    original = path.read_text(encoding="utf-8")
    updated, count = replace_symbol_spans(original, mapping)
    if count and not dry_run and updated != original:
        path.write_text(updated, encoding="utf-8")
    return count


# ---------------------------------------------------------------------------
# Local file tests (nested tags + leftover CSS)
# ---------------------------------------------------------------------------

TEST_SAMPLE = """\
<p><span lang=EN-US style='font-family:Symbol'>a</span></p>
<p><span style='font-family:Symbol'>b</span></p>
<p><span lang=EN-US style='font-family:Symbol;font-style:normal'>&acute;</span></p>
<p><span lang=EN-US style='font-family:Symbol;letter-spacing:1.0pt'>x</span></p>
<p><span lang=EN-US style='font-size:14.0pt;font-family:Symbol'>l</span></p>
<p><i><sub><span lang=EN-US style='font-family:Symbol'><img
width=12 height=15
src="res/example.gif"
u1:shapes="_x0000_i1028" align=absmiddle></span></sub></i></p>
<p><span lang=EN-US style='font-family:Symbol'>j<i>-</i></span></p>
<p><span lang=EN-US style='font-family:Symbol'>&pound;q&pound;</span></p>
<p><span lang=EN-US style='font-family:Symbol'>&nbsp;r</span></p>
<p><span
lang=EN-US style='font-family:
Symbol'>S</span></p>
"""

TEST_EXPECTED = """\
<p><span lang=EN-US>α</span></p>
<p>β</p>
<p><span lang=EN-US style='font-style:normal'>×</span></p>
<p><span lang=EN-US style='letter-spacing:1.0pt'>ξ</span></p>
<p><span lang=EN-US style='font-size:14.0pt'>λ</span></p>
<p><i><sub><span lang=EN-US><img
width=12 height=15
src="res/example.gif"
u1:shapes="_x0000_i1028" align=absmiddle></span></sub></i></p>
<p><span lang=EN-US>ϕ<i>−</i></span></p>
<p><span lang=EN-US>≤θ≤</span></p>
<p><span lang=EN-US>&nbsp;ρ</span></p>
<p><span
lang=EN-US>Σ</span></p>
"""


def run_self_test(mapping: Dict[int, str], tmp_dir: Path) -> int:
    sample_path = tmp_dir / "symbol_sample.md"
    sample_path.write_text(TEST_SAMPLE, encoding="utf-8")
    got, count = replace_symbol_spans(TEST_SAMPLE, mapping)
    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        if cond:
            print(f"  PASS  {name}")
        else:
            failures += 1
            print(f"  FAIL  {name}")
            if detail:
                print(detail)

    check("rewrote every Symbol span", count == 10, f"count={count}")
    check("output matches expected fixture", got == TEST_EXPECTED, f"--- got ---\n{got}\n--- expected ---\n{TEST_EXPECTED}")
    check("nested <img> preserved verbatim", "src=\"res/example.gif\"" in got and "<img\nwidth=12 height=15" in got)
    check("nested <i> tags preserved", "<i>−</i>" in got)
    check("font-style retained", "style='font-style:normal'" in got)
    check("letter-spacing retained", "style='letter-spacing:1.0pt'" in got)
    check("font-size retained", "style='font-size:14.0pt'" in got)
    check("empty style dropped and span unwrapped", "<p>β</p>" in got)
    check("lang attribute kept when present", "<span lang=EN-US>α</span>" in got)
    check("no leftover Symbol family", "font-family:Symbol" not in got.replace(" ", ""))
    check("HTML entities mapped (&pound; → ≤)", "≤θ≤" in got)
    check("nbsp not mapped to euro", "&nbsp;ρ" in got)

    # Round-trip the temp file the same way the batch job writes files.
    process_file(sample_path, mapping, dry_run=False)
    on_disk = sample_path.read_text(encoding="utf-8")
    check("local file write matches in-memory result", on_disk == TEST_EXPECTED)

    if failures:
        print(f"\n{failures} test(s) failed")
        return 1
    print("\nall local file tests passed")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory to scan for .md files",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="path to Adobe symbol.txt (default: <root>/symbol.txt)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="do not attempt to download the mapping; use the local file only",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test",
        action="store_true",
        help="run the nested-tag / CSS preservation tests and exit",
    )
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)

    mapping_path = args.mapping or (args.root / "symbol.txt")
    mapping = load_mapping(mapping_path, download=not args.no_download)

    if args.test:
        tmp_dir = args.root / ".symbol_test"
        tmp_dir.mkdir(exist_ok=True)
        try:
            return run_self_test(mapping, tmp_dir)
        finally:
            sample = tmp_dir / "symbol_sample.md"
            if sample.exists():
                sample.unlink()
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    if args.files:
        paths = args.files
    else:
        paths = list(iter_markdown_files(args.root))

    changed_files = 0
    total_spans = 0
    for path in paths:
        count = process_file(path, mapping, dry_run=args.dry_run)
        if count:
            changed_files += 1
            total_spans += count
            print(f"{path}: {count} Symbol span(s)")
    print(f"{changed_files} file(s), {total_spans} span(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
