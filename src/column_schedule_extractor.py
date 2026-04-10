# """
# Generalized Column Schedule PDF Extraction Pipeline
# ====================================================

# Fully dynamic extraction — ZERO hard-coded pixel coordinates.

# Works with any structural-engineering column schedule PDF that
# follows the same visual pattern as pattern-11.pdf, regardless of:
#   • Number of floors  (rows in the data grid)
#   • Number of columns (C1, C2, C2A … any count, any width)
#   • Page size / DPI

# APPROACH
# --------
# 1. Render PDF page to a high-resolution image (600 DPI by default).
# 2. Detect horizontal grid lines on the full image → find the
#    longest run of evenly-spaced lines (= data rows / floors).
# 3. Re-detect vertical lines scoped to the table's Y range →
#    cluster them (= data column boundaries, including wide cells).
# 4. Locate the column-header row (just above the first data row)
#    and the floor-label column (just left of the first data col).
# 5. Send each header / label cell to a vision LLM to read labels.
# 6. Send each data cell to the vision LLM for structured extraction.
# 7. Assemble everything into the same JSON schema as the original.

# USAGE
# -----
#     python pattern11_extraction_pipeline_generalized.py \\
#         --pdf  pattern-11.pdf        \\
#         --output extracted.json      \\
#         [--api claude|openai]        \\
#         [--dpi 600]                  \\
#         [--upscale 2]                \\
#         [--debug]                    # saves a grid-overlay PNG

# DEPENDENCIES
# ------------
#     pip install pdf2image Pillow scipy numpy anthropic openai
#     sudo apt-get install poppler-utils   # or brew install poppler
# """

# import argparse
# import base64
# import json
# import re
# from io import BytesIO
# from pathlib import Path

# import numpy as np
# from PIL import Image, ImageDraw
# from pdf2image import convert_from_path
# from scipy.signal import find_peaks


# # ══════════════════════════════════════════════════════════════════════════════
# # §1  PDF → Image
# # ══════════════════════════════════════════════════════════════════════════════

# def pdf_to_image(pdf_path: str, dpi: int = 600) -> Image.Image:
#     """Render the first page of a PDF at the given DPI."""
#     return convert_from_path(pdf_path, dpi=dpi)[0]


# # ══════════════════════════════════════════════════════════════════════════════
# # §2  Grid-Line Detection
# # ══════════════════════════════════════════════════════════════════════════════

# def _darkness_peaks(arr2d: np.ndarray,
#                     threshold: float,
#                     min_distance: int) -> list:
#     """
#     Given a 2-D float array (rows × cols), return the row indices where
#     the fraction of 'dark' pixels (value < 100) exceeds `threshold`.
#     """
#     darkness = np.mean(arr2d < 100, axis=1)
#     peaks, _ = find_peaks(darkness, height=threshold, distance=min_distance)
#     return peaks.tolist()


# def detect_lines(img: Image.Image,
#                  y0: int = 0,   y1: int = -1,
#                  x0: int = 0,   x1: int = -1,
#                  h_thr: float = 0.15,
#                  v_thr: float = 0.15,
#                  min_dist: int = 30) -> tuple:
#     """
#     Detect horizontal and vertical dark lines within a sub-region of `img`.

#     Parameters
#     ----------
#     y0, y1 : row slice (absolute pixels; -1 → end of image)
#     x0, x1 : col slice (absolute pixels; -1 → end of image)
#     h_thr  : minimum dark-pixel fraction to count as a horizontal line
#     v_thr  : minimum dark-pixel fraction to count as a vertical line
#     min_dist: minimum pixel gap between two detected peaks

#     Returns
#     -------
#     (h_lines, v_lines) — absolute pixel positions
#     """
#     arr = np.array(img, dtype=float)
#     if y1 < 0:
#         y1 = arr.shape[0]
#     if x1 < 0:
#         x1 = arr.shape[1]

#     region = arr[y0:y1, x0:x1, :3]

#     # Convert to 2-D grayscale (H × W) before peak detection.
#     # Without this, region is 3-D (H × W × 3) and np.mean(…, axis=1)
#     # returns a 2-D array, causing scipy's find_peaks to raise
#     # "ValueError: `x` must be a 1-D array".
#     gray = np.mean(region, axis=2)   # shape: (H, W)

#     h_raw = _darkness_peaks(gray,    h_thr, min_dist)
#     v_raw = _darkness_peaks(gray.T,  v_thr, min_dist)  # transpose → cols become rows

#     return [p + y0 for p in h_raw], [p + x0 for p in v_raw]


# def cluster_lines(lines: list, gap: int = 12) -> list:
#     """
#     Merge groups of nearby lines (within `gap` pixels) into a single
#     representative position (the mean of the group).
#     Removes duplicates introduced by thick grid borders.
#     """
#     if not lines:
#         return []
#     lines = sorted(lines)
#     clusters, group = [], [lines[0]]
#     for x in lines[1:]:
#         if x - group[-1] <= gap:
#             group.append(x)
#         else:
#             clusters.append(int(round(np.mean(group))))
#             group = [x]
#     clusters.append(int(round(np.mean(group))))
#     return clusters


# def find_regular_grid(lines: list, tolerance: float = 0.35) -> list:
#     """
#     Return the **longest contiguous run** of lines whose inter-line
#     gaps are all within `tolerance` (±35 %) of the median gap.

#     Why: data rows have uniform height (one gap per floor).
#     Title, sub-headers, legend rows have very different heights
#     → they break the streak and are excluded automatically.

#     Parameters
#     ----------
#     lines     : sorted list of line positions (px)
#     tolerance : fractional tolerance on median gap (0.35 = 35 %)

#     Returns
#     -------
#     Subset of `lines` that forms the most uniform grid.
#     """
#     if len(lines) < 3:
#         return lines

#     lines = sorted(lines)
#     gaps  = [lines[i + 1] - lines[i] for i in range(len(lines) - 1)]
#     med   = float(np.median(gaps))

#     best_s, best_n = 0, 0
#     cur_s,  cur_n  = 0, 1

#     for i, g in enumerate(gaps):
#         if abs(g - med) / (med + 1e-9) <= tolerance:
#             cur_n += 1
#         else:
#             if cur_n > best_n:
#                 best_s, best_n = cur_s, cur_n
#             cur_s, cur_n = i + 1, 1

#     if cur_n > best_n:
#         best_s, best_n = cur_s, cur_n

#     return lines[best_s: best_s + best_n + 1]


# def find_best_grid(lines: list,
#                    min_rows: int = 3,
#                    tolerance: float = 0.35) -> list:
#     """
#     Find the grid scale that covers the MAXIMUM vertical (or horizontal) span.

#     Problem with find_regular_grid() on wide PDFs (e.g. 6.pdf with 11 columns):
#     ─────────────────────────────────────────────────────────────────────────
#     Each floor cell contains internal horizontal dividers for SIZE / CONC.MIX /
#     VERT.REINF / RING sub-rows.  On narrow PDFs (3 columns) these dividers span
#     only ~30 % of the page width → darkness < threshold → not detected.
#     On wide PDFs (11 columns) they span ~60 % → ARE detected.

#     find_regular_grid() uses the GLOBAL MEDIAN gap, so when many short sub-row
#     gaps dominate the median (~175 px), it finds the longest run of those tiny
#     gaps instead of the 8 large floor rows (~700 px each).

#     Solution:
#     ─────────────────────────────────────────────────────────────────────────
#     Try EVERY unique gap value as a candidate "row height".  For each candidate,
#     find the longest consistent run.  Return the run with MAXIMUM COVERAGE
#     (total pixels spanned).

#     The 8 major floor rows span  8 × 700 px = 5 600 px.
#     Internal sub-row runs span   3 × 175 px =   525 px  (at most one floor).
#     Maximum-coverage selection therefore reliably picks the floor-level grid.

#     Parameters
#     ----------
#     lines     : sorted list of detected line positions (pixels)
#     min_rows  : discard candidates with fewer rows than this
#     tolerance : ±fraction around candidate gap (0.35 = ±35 %)
#     """
#     if len(lines) < min_rows + 1:
#         return lines

#     lines = sorted(lines)
#     gaps  = [lines[i + 1] - lines[i] for i in range(len(lines) - 1)]

#     best_lines    = []
#     best_coverage = 0

#     def _evaluate(s: int, n: int) -> None:
#         nonlocal best_lines, best_coverage
#         if n < min_rows:
#             return
#         run      = lines[s: s + n + 1]
#         coverage = run[-1] - run[0]
#         if coverage > best_coverage:
#             best_coverage = coverage
#             best_lines    = run

#     for target in set(gaps):          # try every unique gap as a candidate row height
#         cur_s, cur_n = 0, 1
#         for i, g in enumerate(gaps):
#             if abs(g - target) / (target + 1e-9) <= tolerance:
#                 cur_n += 1
#             else:
#                 _evaluate(cur_s, cur_n)
#                 cur_s, cur_n = i + 1, 1
#         _evaluate(cur_s, cur_n)

#     return best_lines if best_lines else lines   # fallback: return all lines


# def extend_grid_with_trailing_lines(grid: list,
#                                     all_lines: list,
#                                     max_extra_multiplier: float = 4.0) -> list:
#     """
#     After finding the regular grid (e.g. uniform columns C1–C10),
#     check whether there are extra vertical lines to the right that
#     weren't captured because they form a wider column (e.g. C11).

#     Any line beyond `grid[-1]` and within
#     `max_extra_multiplier × median_gap` is appended.
#     """
#     if not grid or len(grid) < 2:
#         return grid

#     med_gap = np.median([grid[i + 1] - grid[i] for i in range(len(grid) - 1)])
#     last    = grid[-1]

#     extras = sorted(
#         x for x in all_lines
#         if last < x <= last + max_extra_multiplier * med_gap
#     )

#     return grid + extras


# # ══════════════════════════════════════════════════════════════════════════════
# # §3  Vision API Helpers
# # ══════════════════════════════════════════════════════════════════════════════

# def _to_b64(img: Image.Image) -> str:
#     """Convert a PIL Image to a base-64 PNG string."""
#     buf = BytesIO()
#     img.save(buf, format="PNG")
#     return base64.b64encode(buf.getvalue()).decode()


# def get_client(api: str):
#     """Instantiate the appropriate API client from environment variables."""
#     if api == "claude":
#         try:
#             from anthropic import Anthropic
#             return Anthropic()
#         except ImportError:
#             raise RuntimeError("Run: pip install anthropic")
#     else:
#         try:
#             from openai import OpenAI
#             return OpenAI()
#         except ImportError:
#             raise RuntimeError("Run: pip install openai")


# def call_vision(client,
#                 api:        str,
#                 img:        Image.Image,
#                 prompt:     str,
#                 max_tokens: int = 500) -> str:
#     """
#     Send `img` + `prompt` to Claude or GPT-4o and return the text response.
#     Handles both APIs transparently.
#     """
#     b64 = _to_b64(img)

#     if api == "claude":
#         resp = client.messages.create(
#             model="claude-sonnet-4-20250514",
#             max_tokens=max_tokens,
#             messages=[{
#                 "role": "user",
#                 "content": [
#                     {
#                         "type": "image",
#                         "source": {
#                             "type": "base64",
#                             "media_type": "image/png",
#                             "data": b64,
#                         },
#                     },
#                     {"type": "text", "text": prompt},
#                 ],
#             }],
#         )
#         return resp.content[0].text

#     # ── OpenAI ────────────────────────────────────────────────────────────────
#     resp = client.chat.completions.create(
#         model="gpt-4o",
#         max_tokens=max_tokens,
#         messages=[{
#             "role": "user",
#             "content": [
#                 {
#                     "type": "image_url",
#                     "image_url": {
#                         "url":    f"data:image/png;base64,{b64}",
#                         "detail": "high",
#                     },
#                 },
#                 {"type": "text", "text": prompt},
#             ],
#         }],
#     )
#     return resp.choices[0].message.content


# # ══════════════════════════════════════════════════════════════════════════════
# # §4  Label Extraction
# # ══════════════════════════════════════════════════════════════════════════════

# def _crop_upscale(img: Image.Image,
#                   x0: int, y0: int, x1: int, y1: int,
#                   upscale: int = 2) -> Image.Image:
#     """Crop a region and upscale it for clearer text recognition."""
#     cell = img.crop((x0, y0, x1, y1))
#     if upscale > 1:
#         cell = cell.resize(
#             (cell.width * upscale, cell.height * upscale),
#             Image.LANCZOS,
#         )
#     return cell


# def extract_col_labels(client, api: str, img: Image.Image,
#                         header_y0: int, header_y1: int,
#                         col_x: list,
#                         upscale: int = 2) -> list:
#     """
#     Read the column identifier from each cell in the header row.

#     `col_x` is a list of N+1 x-positions bounding N data columns.
#     Returns a list of N label strings.
#     """
#     prompt = (
#         "This is a single header cell from a structural engineering column "
#         "schedule. Return ONLY the column identifier label exactly as it "
#         "appears (e.g. C1, C2, C2A, D4, C10, C11). "
#         "If the cell is empty or not a column label, return 'SKIP'."
#     )
#     labels = []
#     for i in range(len(col_x) - 1):
#         cell = _crop_upscale(img, col_x[i], header_y0, col_x[i + 1], header_y1, upscale)
#         raw  = call_vision(client, api, cell, prompt, max_tokens=50).strip().strip('"').strip("'")
#         # Accept anything short and non-empty; fall back to positional name
#         labels.append(raw if raw and raw != "SKIP" and len(raw) <= 15 else f"COL_{i + 1}")
#     return labels


# def extract_floor_labels(client, api: str, img: Image.Image,
#                           label_x0: int, label_x1: int,
#                           row_y: list,
#                           upscale: int = 2) -> list:
#     """
#     Read the floor/level name from each cell in the label column.

#     `row_y` is a list of N+1 y-positions bounding N data rows.

#     Returns a list of N strings. Each entry is either:
#       • The floor label as written  (e.g. "Ground Floor Column")
#       • "SKIP"   — the cell is NOT a floor data row (FOOTING section,
#                    legend, notes, repeat-header, or empty)
#       • "Floor_N" — the cell looks like a floor row but the text was
#                    unreadable; will be re-checked by filter_and_recheck_floor_rows()
#     """
#     prompt = (
#         "This is a single row-header cell from a structural engineering "
#         "column schedule. The text may be printed vertically.\n\n"
#         "TASK: Determine whether this cell contains a floor/level label.\n\n"
#         "Floor labels look like:\n"
#         "  '6th FLOOR COLUMN', '5th FLOOR COLUMN', '4th FLOOR COLUMN',\n"
#         "  '3rd FLOOR COLUMN', '2nd FLOOR COLUMN', '1st FLOOR COLUMN',\n"
#         "  'GROUND FLOOR COLUMN', 'BASE FLOOR COLUMN',\n"
#         "  'BASEMENT FLOOR COLUMN', 'Base Floor Column'.\n"
#         "  NOTE: 'BASE' is an abbreviation for BASEMENT.\n\n"
#         "NON-floor cells look like:\n"
#         "  'FOOTING', 'PEDESTAL', 'NOTES', blank, or a repeat of the "
#         "column header row.\n\n"
#         "If the cell IS a floor label → return the label text EXACTLY as written.\n"
#         "If the cell is NOT a floor label → return exactly the word SKIP.\n"
#         "Return nothing else."
#     )
#     labels = []
#     for i in range(len(row_y) - 1):
#         cell = _crop_upscale(img, label_x0, row_y[i], label_x1, row_y[i + 1], upscale)
#         raw  = call_vision(client, api, cell, prompt, max_tokens=60).strip().strip('"').strip("'")
#         if raw.upper() == "SKIP":
#             labels.append("SKIP")
#         elif raw:
#             labels.append(raw)
#         else:
#             labels.append(f"Floor_{i + 1}")   # unreadable — recheck later
#     return labels


# def filter_and_recheck_floor_rows(client, api: str, img: Image.Image,
#                                    label_x0: int, label_x1: int,
#                                    h_grid: list,
#                                    raw_labels: list,
#                                    upscale: int = 2) -> tuple:
#     """
#     Post-process the raw floor labels returned by extract_floor_labels():

#     • "SKIP"    → drop the row entirely (FOOTING section, legend, etc.)
#     • "Floor_N" → send a second, simpler vision call to confirm the row
#                   is a real floor row and re-read its label.
#     • Anything else → keep as-is.

#     Returns
#     -------
#     row_bounds   : list of (y_start, y_end) tuples — one per VALID row
#     floor_labels : list of label strings — same length as row_bounds
#     """
#     row_bounds   = []
#     floor_labels = []

#     for i, label in enumerate(raw_labels):
#         y0, y1 = h_grid[i], h_grid[i + 1]

#         # ── Explicit SKIP from first pass ────────────────────────────────────
#         if label == "SKIP":
#             print(f"       Row {i + 1} ({y0}–{y1} px): excluded (non-floor section)")
#             continue

#         # ── Successfully read label ──────────────────────────────────────────
#         if not label.startswith("Floor_"):
#             row_bounds.append((y0, y1))
#             floor_labels.append(label)
#             continue

#         # ── Fallback label "Floor_N" — verify and re-read ────────────────────
#         cell = _crop_upscale(img, label_x0, y0, label_x1, y1, upscale)

#         # Step A: confirm it really is a floor row
#         check = call_vision(
#             client, api, cell,
#             "Does this cell contain a floor or level name (any floor from "
#             "basement up to 6th floor or higher)? Answer YES or NO only.",
#             max_tokens=5,
#         ).strip().upper()

#         if "YES" not in check:
#             print(f"       Row {i + 1} ({y0}–{y1} px): excluded (confirmed non-floor)")
#             continue

#         # Step B: re-read the label with a focused retry prompt
#         retry = call_vision(
#             client, api, cell,
#             "Read the floor/level label written in this cell (text may be "
#             "vertical). Examples: 'BASE. FLOOR COLUMN', 'BASEMENT FLOOR "
#             "COLUMN', 'Base Floor Column'. Return ONLY the label text.",
#             max_tokens=60,
#         ).strip().strip('"').strip("'")

#         final_label = retry if retry and len(retry) < 50 else label
#         print(f"       Row {i + 1} ({y0}–{y1} px): re-read as '{final_label}'")
#         row_bounds.append((y0, y1))
#         floor_labels.append(final_label)

#     return row_bounds, floor_labels


# # ══════════════════════════════════════════════════════════════════════════════
# # §5  Data Cell Extraction
# # ══════════════════════════════════════════════════════════════════════════════

# _EMPTY_CELL = {
#     "SIZE":      "---",
#     "CONC_MIX":  "---",
#     "VERT_REINF":"---",
#     "RING":      "---",
# }


# def extract_data_cell(client, api: str, img: Image.Image,
#                        floor: str, column: str) -> dict:
#     """
#     Send a single data cell image to the vision model and return a
#     structured dict with SIZE, CONC_MIX, VERT_REINF, RING.
#     """
#     prompt = (
#         f"Extract ALL text from this structural engineering column schedule cell.\n"
#         f"Floor: {floor}, Column: {column}.\n\n"
#         "Fields to extract (copy exactly as written):\n"
#         '- SIZE        (e.g. "330 X 1100" or "L SHAPE")\n'
#         '- CONC. MIX   (e.g. "M25" or "M30")\n'
#         '- VERT. REINF.(e.g. "4-20 TOR + 12-16 TOR")\n'
#         '- RING        (e.g. "8 TOR 15 @ 75 + @ 150 + 15 @ 75 C/C, 4 SETS + 1 LINK")\n\n'
#         'Return ONLY a JSON object:\n'
#         '{"SIZE":"...","CONC_MIX":"...","VERT_REINF":"...","RING":"..."}\n'
#         'Use "---" for any field that is not visible or unclear.'
#     )
#     text = call_vision(client, api, img, prompt, max_tokens=500)
#     try:
#         m = re.search(r'\{[^{}]+\}', text, re.DOTALL)
#         if m:
#             return json.loads(m.group())
#     except (json.JSONDecodeError, AttributeError):
#         pass
#     return dict(_EMPTY_CELL)


# # ══════════════════════════════════════════════════════════════════════════════
# # §6  Output JSON Parsers
# # ══════════════════════════════════════════════════════════════════════════════

# def parse_size(raw: str) -> dict:
#     """
#     Convert a raw SIZE string into {"width", "depth", "length"}.

#     Examples
#     --------
#     "230 X 600"        → {"width": 230, "depth": None, "length": 600}
#     "230 X 600 X 800"  → {"width": 230, "depth": 600,  "length": 800}
#     "L SHAPE" / "---"  → {"width": None, "depth": None, "length": None}
#     """
#     empty = {"width": None, "depth": None, "length": None}
#     if not raw or raw.strip() in ("---", "", "N/A", "n/a"):
#         return empty

#     parts = re.split(r'\s*[xX×]\s*', raw.strip())
#     nums = []
#     for p in parts:
#         try:
#             nums.append(int(float(p.strip())))
#         except ValueError:
#             pass

#     if len(nums) == 2:
#         return {"width": nums[0], "depth": None, "length": nums[1]}
#     if len(nums) >= 3:
#         return {"width": nums[0], "depth": nums[1], "length": nums[2]}
#     return empty


# def parse_reinforcement(raw: str) -> list:
#     """
#     Convert a raw VERT_REINF string into a list of "N-DT" tokens.

#     Examples
#     --------
#     "6-20 TOR + 4-16 TOR"   → ["6-20T", "4-16T"]
#     "4-20 TOR + 12-16 TOR"  → ["4-20T", "12-16T"]
#     "---"                   → []
#     """
#     if not raw or raw.strip() in ("---", ""):
#         return []

#     result = []
#     for part in re.split(r'\s*\+\s*', raw.strip()):
#         part = part.strip()
#         if not part:
#             continue
#         m = re.match(r'(\d+)\s*[-–]\s*(\d+)\s*(?:TOR|T)\b', part, re.IGNORECASE)
#         if m:
#             result.append(f"{m.group(1)}-{m.group(2)}T")
#         else:
#             nums = re.findall(r'\d+', part)
#             if len(nums) >= 2:
#                 result.append(f"{nums[0]}-{nums[1]}T")
#     return result


# def parse_stirrups(raw: str) -> dict:
#     """
#     Convert a raw RING string into {"dia": [...], "spacing": [...]}.

#     Examples
#     --------
#     "10 TOR 15 @ 75 + @ 115 + 15 @ 75 C/C"
#         → {"dia": ["10-T15"], "spacing": ["75 C/C", "115 C/C"]}
#     "---" → {"dia": [], "spacing": []}
#     """
#     empty: dict = {"dia": [], "spacing": []}
#     if not raw or raw.strip() in ("---", ""):
#         return empty

#     # Diameter tokens: "N TOR M" or "N T M" → "N-TM"
#     dias = [
#         f"{m[0]}-T{m[1]}"
#         for m in re.findall(r'(\d+)\s*(?:TOR|T)\s*(\d+)', raw, re.IGNORECASE)
#     ]

#     # Spacing values: collect unique numbers from "@ N" and "N C/C" patterns
#     seen: set = set()
#     spacings: list = []

#     # Numbers explicitly marked C/C take priority
#     for n in re.findall(r'(\d+)\s*C/C', raw, re.IGNORECASE):
#         if n not in seen:
#             spacings.append(f"{n} C/C")
#             seen.add(n)
#     # Remaining numbers after "@"
#     for n in re.findall(r'@\s*(\d+)', raw):
#         if n not in seen:
#             spacings.append(f"{n} C/C")
#             seen.add(n)

#     return {"dia": dias, "spacing": spacings}


# # ══════════════════════════════════════════════════════════════════════════════
# # §8  Debug Overlay  (optional)
# # ══════════════════════════════════════════════════════════════════════════════

# def save_debug_overlay(img:        Image.Image,
#                         h_grid:     list,
#                         v_grid:     list,
#                         header_band: tuple,
#                         label_band:  tuple,
#                         out_path:   str) -> None:
#     """
#     Save a copy of `img` with the detected grid overlaid in colour:
#       Green lines  → horizontal data-row boundaries
#       Blue  lines  → vertical data-column boundaries
#       Yellow band  → column-header row
#       Orange band  → floor-label column
#     """
#     W, H    = img.size
#     rgba    = img.convert("RGBA")
#     overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
#     dr      = ImageDraw.Draw(overlay)

#     for y in h_grid:
#         dr.line([(0, y), (W, y)], fill=(0, 220, 0, 200), width=3)
#     for x in v_grid:
#         dr.line([(x, 0), (x, H)], fill=(30, 120, 255, 200), width=3)

#     hy0, hy1 = header_band
#     dr.rectangle([0, hy0, W, hy1], fill=(255, 220, 0, 60))

#     lx0, lx1 = label_band
#     dr.rectangle([lx0, 0, lx1, H], fill=(255, 110, 0, 60))

#     Image.alpha_composite(rgba, overlay).convert("RGB").save(out_path)
#     print(f"       Debug overlay → {out_path}")


# # ══════════════════════════════════════════════════════════════════════════════
# # §9  Main Pipeline
# # ══════════════════════════════════════════════════════════════════════════════

# def extract_column_schedule(pdf_path:    str,
#                              output_path: str  = "output.json",
#                              api:         str  = "claude",
#                              dpi:         int  = 600,
#                              upscale:     int  = 2,
#                              debug:       bool = False) -> dict:
#     """
#     Fully generalized extraction pipeline for structural-engineering
#     column schedule PDFs.

#     No hard-coded coordinates — all grid boundaries are discovered
#     automatically from the image.

#     Parameters
#     ----------
#     pdf_path    : Path to the input PDF.
#     output_path : Where to write the JSON result.
#     api         : "claude" (default) or "openai".
#     dpi         : PDF render resolution.  600 is the recommended minimum.
#     upscale     : Per-cell upscaling factor for better OCR (2 = default).
#     debug       : If True, save a colour-coded grid-overlay PNG alongside
#                   the JSON output.

#     Returns
#     -------
#     The extracted data as a Python dict (also written to `output_path`).
#     """
#     client = get_client(api)

#     # ── Step 1 : Render ───────────────────────────────────────────────────────
#     print(f"\n[1/6] Rendering '{Path(pdf_path).name}' at {dpi} DPI …")
#     img  = pdf_to_image(pdf_path, dpi=dpi)
#     W, H = img.size
#     print(f"       Image: {W} × {H} px")

#     # ── Steps 2+3 : Adaptive horizontal line detection → data-row grid ───────────
#     #
#     # WHY ADAPTIVE?
#     # ─────────────
#     # Each floor cell has internal horizontal dividers for SIZE / CONC.MIX /
#     # VERT.REINF / RING sub-rows.  On narrow PDFs (3 columns, ~4 959 px wide)
#     # these sub-row lines span only ~28 % of the page width; at h_thr=0.15 they
#     # may fall below the threshold and NOT be detected.
#     # On wide PDFs (11 columns, ~9 925 px wide) the same dividers span ~55 %
#     # of the page width and ARE detected, flooding h_all with ~30 extra lines.
#     # find_best_grid then picks the wrong scale.
#     #
#     # SOLUTION: try increasing darkness thresholds until the detected grid has
#     # "sensible" floor heights (between image_height/25 and image_height/2).
#     # Major floor-boundary lines are always thicker/darker than sub-row dividers,
#     # so a slightly higher threshold filters out the sub-rows while keeping the
#     # floor boundaries.
#     # ─────────────
#     print("[2/6] Detecting horizontal grid lines (adaptive threshold) …")

#     H_THRESHOLDS  = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
#     min_floor_h   = H / 25      # assume at most 25 floors
#     max_floor_h   = H / 2       # assume at least 2 floors

#     h_all  = []
#     h_grid = []
#     chosen_thr = H_THRESHOLDS[0]

#     for h_thr in H_THRESHOLDS:
#         h_raw, _ = detect_lines(img, h_thr=h_thr, v_thr=0.0, min_dist=30)
#         cand_all  = cluster_lines(sorted(h_raw))

#         if len(cand_all) < 4:          # too few lines to form a grid
#             h_all = cand_all
#             continue

#         cand_grid = find_best_grid(cand_all, min_rows=3, tolerance=0.35)
#         n_cand    = len(cand_grid) - 1

#         if n_cand < 3:
#             h_all  = cand_all
#             h_grid = cand_grid
#             continue

#         avg_h = (cand_grid[-1] - cand_grid[0]) / n_cand

#         # Remember this attempt regardless
#         h_all      = cand_all
#         h_grid     = cand_grid
#         chosen_thr = h_thr

#         if min_floor_h <= avg_h <= max_floor_h:
#             # Found a sensible grid — stop trying higher thresholds
#             break

#     print(f"       {len(h_all)} horizontal lines  (h_thr={chosen_thr})")

#     # ── Step 3 : Report ───────────────────────────────────────────────────────
#     print("[3/6] Identifying data rows …")

#     if len(h_grid) < 3:
#         raise ValueError(
#             f"Only {len(h_grid)} consistent horizontal grid lines found "
#             f"(need ≥ 3 for at least 2 data rows). "
#             "Use --debug to inspect the detected lines."
#         )

#     n_rows = len(h_grid) - 1
#     print(f"       {n_rows} data rows  |  Y: {h_grid[0]} – {h_grid[-1]} px")

#     # ── Step 4 : Detect vertical lines scoped to the table's Y range ──────────
#     # Scoping prevents title / legend content from polluting column detection.
#     print("[4/6] Detecting vertical grid lines within table …")
#     _, v_raw = detect_lines(
#         img,
#         y0=h_grid[0], y1=h_grid[-1],
#         h_thr=0.0, v_thr=0.20, min_dist=30,
#     )
#     v_all = cluster_lines(sorted(v_raw))
#     print(f"       {len(v_all)} vertical lines after clustering")

#     # Use find_regular_grid to locate the tightly-spaced "core" columns,
#     # then extend with any wider columns that follow (e.g. a wide C11).
#     v_core  = find_regular_grid(v_all, tolerance=0.40)
#     v_grid  = extend_grid_with_trailing_lines(v_core, v_all, max_extra_multiplier=4.0)

#     if len(v_grid) < 3:
#         raise ValueError(
#             f"Only {len(v_grid)} consistent vertical grid lines found "
#             f"(need ≥ 3 for label col + ≥ 1 data col). "
#             "Try --debug or adjust thresholds."
#         )

#     # ── Step 5 : Identify the label column and column-header row ──────────────
#     #
#     # Convention for this PDF family:
#     #   • The FIRST band in v_grid  [v_grid[0], v_grid[1]]  is the floor-label
#     #     column (contains floor names).
#     #   • Data columns occupy        v_grid[1:]
#     #
#     # However, some PDFs don't have a visible left border for the label column,
#     # making v_grid[0] the label/data separator rather than the left border.
#     # We distinguish via a threshold: if v_grid[0] is further from the left
#     # page edge than ~half a typical column width, it must be the separator.

#     typical_col_width = float(np.median(
#         [v_grid[i + 1] - v_grid[i] for i in range(len(v_grid) - 1)]
#     ))
#     label_col_threshold = int(typical_col_width * 0.5)  # scales with column width

#     if v_grid[0] <= label_col_threshold:
#         # v_grid[0] is the visible left border of the label column
#         label_x0    = v_grid[0]
#         label_x1    = v_grid[1]
#         data_col_x  = v_grid[1:]   # boundaries for data columns
#     else:
#         # v_grid[0] is the separator; label column runs from near-0 to v_grid[0]
#         label_x0    = max(0, v_grid[0] - int(typical_col_width * 0.9))
#         label_x1    = v_grid[0]
#         data_col_x  = v_grid       # v_grid already starts at first data col boundary

#     n_cols = len(data_col_x) - 1
#     if n_cols < 1:
#         raise ValueError("No data columns detected. Use --debug to inspect.")

#     print(f"       {n_cols} data columns  |  X: {data_col_x[0]} – {data_col_x[-1]} px")

#     # Column-header row: some PDFs put it ABOVE the data grid (3.pdf style),
#     # others put it ONLY at the BOTTOM as a footer row (4.pdf style).
#     # We locate BOTH and try each; whichever yields real column labels wins.

#     above_h = [y for y in h_all if y < h_grid[0]]
#     below_h = [y for y in h_all if y > h_grid[-1]]

#     # Top header band
#     if above_h:
#         header_y0 = above_h[-1]
#     else:
#         header_y0 = max(0, h_grid[0] - (h_grid[1] - h_grid[0]) // 2)
#     header_y1 = h_grid[0]

#     # Bottom footer band (may not exist in all PDFs)
#     if below_h:
#         footer_y0 = h_grid[-1]
#         footer_y1 = below_h[0]
#     else:
#         footer_y0 = footer_y1 = None

#     print(f"       Column-header row  Y: {header_y0} – {header_y1} px")
#     if footer_y0 is not None:
#         print(f"       Column-footer row  Y: {footer_y0} – {footer_y1} px")
#     print(f"       Floor-label column X: {label_x0} – {label_x1} px")

#     # ── Optional debug overlay ────────────────────────────────────────────────
#     if debug:
#         dbg_path = str(Path(output_path).with_suffix(".debug.png"))
#         save_debug_overlay(
#             img, h_grid, v_grid,
#             (header_y0, header_y1),
#             (label_x0,  label_x1),
#             dbg_path,
#         )

#     # ── Step 5 : Extract labels ───────────────────────────────────────────────
#     print("[5/6] Extracting column and floor labels via Vision API …")

#     # Try the top header row first.
#     col_labels = extract_col_labels(
#         client, api, img,
#         header_y0, header_y1,
#         data_col_x, upscale,
#     )

#     # Count how many fell back to positional names (COL_N).
#     fallback_count = sum(1 for lbl in col_labels if lbl.startswith("COL_"))

#     # If more than half are fallbacks AND a footer row exists, try that instead.
#     # This handles PDFs where column labels only appear at the bottom (4.pdf style).
#     if fallback_count > len(col_labels) // 2 and footer_y0 is not None:
#         print(f"       Header row gave {fallback_count} fallback(s) — trying footer row …")
#         footer_labels = extract_col_labels(
#             client, api, img,
#             footer_y0, footer_y1,
#             data_col_x, upscale,
#         )
#         footer_fallbacks = sum(1 for lbl in footer_labels if lbl.startswith("COL_"))
#         if footer_fallbacks < fallback_count:
#             print(f"       Footer row is better ({footer_fallbacks} fallback(s)) — using it.")
#             col_labels = footer_labels
#         else:
#             print(f"       Footer row also gave fallbacks — keeping header row result.")

#     # Raw floor labels — may contain "SKIP" (non-floor rows) or "Floor_N"
#     # (fallback for unreadable cells).
#     raw_floor_labels = extract_floor_labels(
#         client, api, img,
#         label_x0, label_x1,
#         h_grid, upscale,
#     )

#     # Filter out non-floor rows (FOOTING, legend, etc.) and re-read any
#     # fallback "Floor_N" labels with a second targeted vision call.
#     print("       Validating floor rows …")
#     row_bounds, floor_labels = filter_and_recheck_floor_rows(
#         client, api, img,
#         label_x0, label_x1,
#         h_grid, raw_floor_labels, upscale,
#     )

#     # row_bounds is now a list of (y_start, y_end) for VALID rows only.
#     n_rows = len(row_bounds)
#     if n_rows == 0:
#         raise ValueError("No valid floor rows found after filtering. Use --debug to inspect.")

#     # ── Second-pass column-label fallback: try SKIP rows at grid boundary ────
#     #
#     # Some PDFs (e.g. 2.pdf, 4.pdf) place column labels (C1, C2A …) in a
#     # thin row at the very BOTTOM of the data grid.  That row is correctly
#     # labelled SKIP by extract_floor_labels() (it isn't a floor row), so it
#     # is absent from row_bounds — but it is also absent from below_h (no
#     # horizontal line detected below h_grid[-1]), which is why the earlier
#     # footer-fallback path silently does nothing.
#     #
#     # Fix: after floor filtering, if we still have too many COL_N placeholders,
#     # build a list of SKIP rows that sit just below the last valid data row
#     # and try each one as a column-label source.
#     fallback_count = sum(1 for lbl in col_labels if lbl.startswith("COL_"))
#     if fallback_count > len(col_labels) // 2 and row_bounds:
#         last_valid_y1 = row_bounds[-1][1]
#         # Collect SKIP rows whose top edge is at or below the last valid row
#         skipped_bottom = [
#             (h_grid[i], h_grid[i + 1])
#             for i, lbl in enumerate(raw_floor_labels)
#             if lbl == "SKIP" and h_grid[i] >= last_valid_y1 - 50
#         ]
#         for sy0, sy1 in skipped_bottom:
#             print(
#                 f"       Still {fallback_count} COL_N fallback(s) — "
#                 f"trying SKIP row ({sy0}–{sy1} px) as column labels …"
#             )
#             skip_labels = extract_col_labels(
#                 client, api, img, sy0, sy1, data_col_x, upscale
#             )
#             skip_fallbacks = sum(1 for lbl in skip_labels if lbl.startswith("COL_"))
#             if skip_fallbacks < fallback_count:
#                 print(f"       SKIP row is better ({skip_fallbacks} fallback(s)) — using it.")
#                 col_labels    = skip_labels
#                 fallback_count = skip_fallbacks
#                 break
#             else:
#                 print(f"       SKIP row also gave fallbacks — continuing search.")

#     # Pad column labels with positional fallbacks if any were missed.
#     col_labels += [f"COL_{i + 1}" for i in range(len(col_labels), n_cols)]
#     col_labels  = col_labels[:n_cols]

#     print(f"       Columns ({n_cols}): {col_labels}")
#     print(f"       Floors  ({n_rows}): {floor_labels}")

#     # Build dict-safe keys: replace spaces / dots / slashes with underscores.
#     def _to_key(s: str) -> str:
#         return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")

#     col_keys   = [_to_key(l) for l in col_labels]
#     floor_keys = [_to_key(l) for l in floor_labels]

#     # ── Step 6 : Extract every data cell ─────────────────────────────────────
#     total = n_rows * n_cols
#     print(f"[6/6] Extracting {total} data cells via Vision API …")

#     result = {
#         "document": Path(pdf_path).name,
#         "title":    "Column Schedule",
#         "columns":  [],
#     }

#     done = 0
#     for r in range(n_rows):
#         # Use explicit (y0, y1) bounds — row_bounds has non-contiguous gaps
#         # filtered out, so we cannot use h_grid[r]/h_grid[r+1] directly.
#         y0, y1 = row_bounds[r]
#         flabel = floor_labels[r]

#         for c in range(n_cols):
#             x0, x1 = data_col_x[c], data_col_x[c + 1]
#             clabel = col_labels[c]

#             cell = _crop_upscale(img, x0, y0, x1, y1, upscale)
#             done += 1

#             print(
#                 f"       [{done:>3}/{total}]  {flabel} / {clabel} …",
#                 end=" ",
#                 flush=True,
#             )
#             raw = extract_data_cell(client, api, cell, flabel, clabel)
#             print("✓")

#             mix_raw = raw.get("CONC_MIX", "---")
#             entry = {
#                 "column_no":     clabel,
#                 "column_name":   _to_key(flabel),
#                 "size":          parse_size(raw.get("SIZE", "---")),
#                 "reinforcement": parse_reinforcement(raw.get("VERT_REINF", "---")),
#                 "stirrups":      parse_stirrups(raw.get("RING", "---")),
#                 "mix":           mix_raw if mix_raw and mix_raw != "---" else None,
#                 "steel_grade":   None,
#             }
#             result["columns"].append(entry)

#     # ── Save ──────────────────────────────────────────────────────────────────
#     with open(output_path, "w", encoding="utf-8") as f:
#         json.dump(result, f, indent=2, ensure_ascii=False)

#     print(f"\n✅  Done!  {total} cells extracted → {output_path}")
#     return result


# # ══════════════════════════════════════════════════════════════════════════════
# # CLI
# # ══════════════════════════════════════════════════════════════════════════════

# if __name__ == "__main__":
#     ap = argparse.ArgumentParser(
#         description=(
#             "Generalized column schedule PDF extractor — "
#             "no hard-coded coordinates, works for any floor/column count."
#         ),
#         formatter_class=argparse.ArgumentDefaultsHelpFormatter,
#     )
#     ap.add_argument("--pdf",     required=True,
#                     help="Path to the input PDF file")
#     ap.add_argument("--output",  default="output.json",
#                     help="Output JSON file path")
#     ap.add_argument("--api",     choices=["claude", "openai"], default="openai",
#                     help="Vision API to use for text extraction")
#     ap.add_argument("--dpi",     type=int, default=600,
#                     help="PDF render resolution (600 recommended minimum)")
#     ap.add_argument("--upscale", type=int, default=2,
#                     help="Per-cell upscale factor (higher = better OCR, slower)")
#     ap.add_argument("--debug",   action="store_true",
#                     help="Save a colour-coded grid-overlay PNG for inspection")
#     args = ap.parse_args()

#     extract_column_schedule(
#         pdf_path    = args.pdf,
#         output_path = args.output,
#         api         = args.api,
#         dpi         = args.dpi,
#         upscale     = args.upscale,
#         debug       = args.debug,
#     )





"""
Generalized Column Schedule PDF Extraction Pipeline
====================================================

Fully dynamic extraction — ZERO hard-coded pixel coordinates.

Works with any structural-engineering column schedule PDF that
follows the same visual pattern as pattern-11.pdf, regardless of:
  • Number of floors  (rows in the data grid)
  • Number of columns (C1, C2, C2A … any count, any width)
  • Page size / DPI
  • Combined / merged rows where one spec covers all columns

APPROACH
--------
1. Render PDF page to a high-resolution image (600 DPI by default).
2. Detect horizontal grid lines on the full image → adaptive threshold
   loop to find the longest run of evenly-spaced lines (= data rows).
3. Re-detect vertical lines scoped to the table's Y range →
   cluster them (= data column boundaries).
4. Locate the column-header row (above or below the data grid)
   and the floor-label column (left of the first data col).
5. Send each header / label cell to GPT-4o to read labels.
6. For each data row:
   a. Extract individual column cells via GPT-4o.
   b. If >50 % of cells return empty data, the row is a COMBINED row
      (one shared spec for all columns) — re-read the full row width
      as a single image and broadcast the result to every column.
7. Assemble everything into the flat JSON schema.

USAGE
-----
    python column_schedule_extractor.py \\
        --pdf  pattern-11.pdf        \\
        --output extracted.json      \\
        [--dpi 600]                  \\
        [--upscale 2]                \\
        [--debug]                    # saves a grid-overlay PNG

DEPENDENCIES
------------
    pip install pdf2image Pillow scipy numpy openai
    sudo apt-get install poppler-utils   # or brew install poppler
"""

import argparse
import base64
import json
import re
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pdf2image import convert_from_path
from scipy.signal import find_peaks


# ══════════════════════════════════════════════════════════════════════════════
# §1  PDF → Image
# ══════════════════════════════════════════════════════════════════════════════

def pdf_to_image(pdf_path: str, dpi: int = 600) -> Image.Image:
    """Render the first page of a PDF at the given DPI."""
    return convert_from_path(pdf_path, dpi=dpi)[0]


# ══════════════════════════════════════════════════════════════════════════════
# §2  Grid-Line Detection
# ══════════════════════════════════════════════════════════════════════════════

def _darkness_peaks(arr2d: np.ndarray,
                    threshold: float,
                    min_distance: int) -> list:
    """
    Given a 2-D float array (rows × cols), return the row indices where
    the fraction of 'dark' pixels (value < 100) exceeds `threshold`.
    """
    darkness = np.mean(arr2d < 100, axis=1)
    peaks, _ = find_peaks(darkness, height=threshold, distance=min_distance)
    return peaks.tolist()


def detect_lines(img: Image.Image,
                 y0: int = 0,   y1: int = -1,
                 x0: int = 0,   x1: int = -1,
                 h_thr: float = 0.15,
                 v_thr: float = 0.15,
                 min_dist: int = 30) -> tuple:
    """
    Detect horizontal and vertical dark lines within a sub-region of `img`.

    Returns (h_lines, v_lines) as absolute pixel positions.
    """
    arr = np.array(img, dtype=float)
    if y1 < 0:
        y1 = arr.shape[0]
    if x1 < 0:
        x1 = arr.shape[1]

    region = arr[y0:y1, x0:x1, :3]
    gray   = np.mean(region, axis=2)          # (H, W) — avoids 3-D scipy bug

    h_raw = _darkness_peaks(gray,   h_thr, min_dist)
    v_raw = _darkness_peaks(gray.T, v_thr, min_dist)

    return [p + y0 for p in h_raw], [p + x0 for p in v_raw]


def cluster_lines(lines: list, gap: int = 12) -> list:
    """Merge groups of nearby lines (within `gap` px) into one representative."""
    if not lines:
        return []
    lines = sorted(lines)
    clusters, group = [], [lines[0]]
    for x in lines[1:]:
        if x - group[-1] <= gap:
            group.append(x)
        else:
            clusters.append(int(round(np.mean(group))))
            group = [x]
    clusters.append(int(round(np.mean(group))))
    return clusters


def find_regular_grid(lines: list, tolerance: float = 0.35) -> list:
    """
    Return the longest contiguous run of lines whose inter-line gaps are
    all within `tolerance` (±35 %) of the median gap.
    """
    if len(lines) < 3:
        return lines

    lines = sorted(lines)
    gaps  = [lines[i + 1] - lines[i] for i in range(len(lines) - 1)]
    med   = float(np.median(gaps))

    best_s, best_n = 0, 0
    cur_s,  cur_n  = 0, 1

    for i, g in enumerate(gaps):
        if abs(g - med) / (med + 1e-9) <= tolerance:
            cur_n += 1
        else:
            if cur_n > best_n:
                best_s, best_n = cur_s, cur_n
            cur_s, cur_n = i + 1, 1

    if cur_n > best_n:
        best_s, best_n = cur_s, cur_n

    return lines[best_s: best_s + best_n + 1]


def find_best_grid(lines: list,
                   min_rows: int = 3,
                   tolerance: float = 0.35) -> list:
    """
    Find the grid scale that covers the MAXIMUM vertical span.
    Handles wide PDFs where internal sub-row dividers flood the line list.
    """
    if len(lines) < min_rows + 1:
        return lines

    lines = sorted(lines)
    gaps  = [lines[i + 1] - lines[i] for i in range(len(lines) - 1)]

    best_lines    = []
    best_coverage = 0

    def _evaluate(s: int, n: int) -> None:
        nonlocal best_lines, best_coverage
        if n < min_rows:
            return
        run      = lines[s: s + n + 1]
        coverage = run[-1] - run[0]
        if coverage > best_coverage:
            best_coverage = coverage
            best_lines    = run

    for target in set(gaps):
        cur_s, cur_n = 0, 1
        for i, g in enumerate(gaps):
            if abs(g - target) / (target + 1e-9) <= tolerance:
                cur_n += 1
            else:
                _evaluate(cur_s, cur_n)
                cur_s, cur_n = i + 1, 1
        _evaluate(cur_s, cur_n)

    return best_lines if best_lines else lines


def extend_grid_with_trailing_lines(grid: list,
                                    all_lines: list,
                                    max_extra_multiplier: float = 4.0) -> list:
    """
    After finding the regular vertical grid (uniform columns), append any
    extra lines to the right that form a wider final column.
    """
    if not grid or len(grid) < 2:
        return grid

    med_gap = np.median([grid[i + 1] - grid[i] for i in range(len(grid) - 1)])
    last    = grid[-1]

    extras = sorted(
        x for x in all_lines
        if last < x <= last + max_extra_multiplier * med_gap
    )
    return grid + extras


# ══════════════════════════════════════════════════════════════════════════════
# §3  Vision API (OpenAI / GPT-4o only)
# ══════════════════════════════════════════════════════════════════════════════

def _to_b64(img: Image.Image) -> str:
    """Convert a PIL Image to a base-64 PNG string."""
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def get_client():
    """Instantiate the OpenAI client (reads OPENAI_API_KEY from env)."""
    try:
        from openai import OpenAI
        return OpenAI()
    except ImportError:
        raise RuntimeError("Run: pip install openai")


def call_vision(client,
                img:        Image.Image,
                prompt:     str,
                max_tokens: int = 500) -> str:
    """Send `img` + `prompt` to GPT-4o and return the text response."""
    b64  = _to_b64(img)
    resp = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url":    f"data:image/png;base64,{b64}",
                        "detail": "high",
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return resp.choices[0].message.content


# ══════════════════════════════════════════════════════════════════════════════
# §4  Label Extraction
# ══════════════════════════════════════════════════════════════════════════════

def _crop_upscale(img: Image.Image,
                  x0: int, y0: int, x1: int, y1: int,
                  upscale: int = 2) -> Image.Image:
    """Crop a region and upscale it for clearer text recognition."""
    cell = img.crop((x0, y0, x1, y1))
    if upscale > 1:
        cell = cell.resize(
            (cell.width * upscale, cell.height * upscale),
            Image.LANCZOS,
        )
    return cell


def extract_col_labels(client, img: Image.Image,
                        header_y0: int, header_y1: int,
                        col_x: list,
                        upscale: int = 2) -> list:
    """
    Read the column identifier from each cell in the header row.
    Returns a list of N label strings for N data columns.
    """
    prompt = (
        "This is a single header cell from a structural engineering column "
        "schedule. Return ONLY the column identifier label exactly as it "
        "appears (e.g. C1, C2, C2A, D4, C10, C11). "
        "If the cell is empty or not a column label, return 'SKIP'."
    )
    labels = []
    for i in range(len(col_x) - 1):
        cell = _crop_upscale(img, col_x[i], header_y0, col_x[i + 1], header_y1, upscale)
        raw  = call_vision(client, cell, prompt, max_tokens=50).strip().strip('"').strip("'")
        labels.append(raw if raw and raw != "SKIP" and len(raw) <= 15 else f"COL_{i + 1}")
    return labels


def extract_floor_labels(client, img: Image.Image,
                          label_x0: int, label_x1: int,
                          row_y: list,
                          upscale: int = 2) -> list:
    """
    Read the floor/level name from each cell in the label column.
    Returns a list of N strings: floor label, "SKIP", or "Floor_N" fallback.
    """
    prompt = (
        "This is a single row-header cell from a structural engineering "
        "column schedule. The text may be printed vertically.\n\n"
        "TASK: Determine whether this cell contains a floor/level label.\n\n"
        "Floor labels look like:\n"
        "  '6th FLOOR COLUMN', '5th FLOOR COLUMN', '4th FLOOR COLUMN',\n"
        "  '3rd FLOOR COLUMN', '2nd FLOOR COLUMN', '1st FLOOR COLUMN',\n"
        "  'GROUND FLOOR COLUMN', 'BASE FLOOR COLUMN',\n"
        "  'BASEMENT FLOOR COLUMN', 'Base Floor Column'.\n"
        "  NOTE: 'BASE' is an abbreviation for BASEMENT.\n\n"
        "NON-floor cells look like:\n"
        "  'FOOTING', 'PEDESTAL', 'NOTES', blank, or a repeat of the "
        "column header row.\n\n"
        "If the cell IS a floor label → return the label text EXACTLY as written.\n"
        "If the cell is NOT a floor label → return exactly the word SKIP.\n"
        "Return nothing else."
    )
    labels = []
    for i in range(len(row_y) - 1):
        cell = _crop_upscale(img, label_x0, row_y[i], label_x1, row_y[i + 1], upscale)
        raw  = call_vision(client, cell, prompt, max_tokens=60).strip().strip('"').strip("'")
        if raw.upper() == "SKIP":
            labels.append("SKIP")
        elif raw:
            labels.append(raw)
        else:
            labels.append(f"Floor_{i + 1}")
    return labels


def filter_and_recheck_floor_rows(client, img: Image.Image,
                                   label_x0: int, label_x1: int,
                                   h_grid: list,
                                   raw_labels: list,
                                   upscale: int = 2) -> tuple:
    """
    Post-process raw floor labels:
      • "SKIP"    → drop the row (FOOTING, legend, etc.)
      • "Floor_N" → second vision call to confirm and re-read label
      • Anything else → keep as-is

    Returns (row_bounds, floor_labels) for VALID rows only.
    """
    row_bounds   = []
    floor_labels = []

    for i, label in enumerate(raw_labels):
        y0, y1 = h_grid[i], h_grid[i + 1]

        if label == "SKIP":
            print(f"       Row {i + 1} ({y0}–{y1} px): excluded (non-floor section)")
            continue

        if not label.startswith("Floor_"):
            row_bounds.append((y0, y1))
            floor_labels.append(label)
            continue

        # Fallback label — verify and re-read
        cell = _crop_upscale(img, label_x0, y0, label_x1, y1, upscale)

        check = call_vision(
            client, cell,
            "Does this cell contain a floor or level name (any floor from "
            "basement up to 6th floor or higher)? Answer YES or NO only.",
            max_tokens=5,
        ).strip().upper()

        if "YES" not in check:
            print(f"       Row {i + 1} ({y0}–{y1} px): excluded (confirmed non-floor)")
            continue

        retry = call_vision(
            client, cell,
            "Read the floor/level label written in this cell (text may be "
            "vertical). Examples: 'BASE. FLOOR COLUMN', 'BASEMENT FLOOR "
            "COLUMN', 'Base Floor Column'. Return ONLY the label text.",
            max_tokens=60,
        ).strip().strip('"').strip("'")

        final_label = retry if retry and len(retry) < 50 else label
        print(f"       Row {i + 1} ({y0}–{y1} px): re-read as '{final_label}'")
        row_bounds.append((y0, y1))
        floor_labels.append(final_label)

    return row_bounds, floor_labels


# ══════════════════════════════════════════════════════════════════════════════
# §5  Data Cell Extraction
# ══════════════════════════════════════════════════════════════════════════════

_EMPTY_CELL = {
    "SIZE":       "---",
    "CONC_MIX":   "---",
    "VERT_REINF": "---",
    "RING":       "---",
}

_DATA_PROMPT_TEMPLATE = (
    "Extract ALL text from this structural engineering column schedule cell.\n"
    "Floor: {floor}, Column: {column}.\n\n"
    "Fields to extract (copy exactly as written):\n"
    '- SIZE        (e.g. "330 X 1100" or "L SHAPE" or "AS PER PLAN")\n'
    '- CONC. MIX   (e.g. "M25" or "M30")\n'
    '- VERT. REINF.(e.g. "4-20 TOR + 12-16 TOR")\n'
    '- RING        (e.g. "8 TOR 15 @ 75 + @ 150 + 15 @ 75 C/C, 4 SETS + 1 LINK")\n\n'
    'Return ONLY a JSON object:\n'
    '{{"SIZE":"...","CONC_MIX":"...","VERT_REINF":"...","RING":"..."}}\n'
    'Use "---" for any field that is not visible or unclear.'
)

_COMBINED_PROMPT_TEMPLATE = (
    "This is a FULL-WIDTH row from a structural engineering column schedule.\n"
    "Floor: {floor}.\n\n"
    "This row contains ONE shared specification that applies to ALL columns.\n"
    "Extract the following fields (copy exactly as written):\n"
    '- SIZE        (e.g. "AS PER PLAN", "330 X 1100")\n'
    '- CONC. MIX   (e.g. "M25" or "M30")\n'
    '- VERT. REINF.(e.g. "49-25 TOR")\n'
    '- RING        (e.g. "10 TOR 15 @ 75 + @ 115 + 15 @ 75 C/C, 9 SETS + 9 LINKS")\n\n'
    'Return ONLY a JSON object:\n'
    '{{"SIZE":"...","CONC_MIX":"...","VERT_REINF":"...","RING":"..."}}\n'
    'Use "---" for any field that is not visible or unclear.'
)


def _parse_json_response(text: str) -> dict:
    """Extract the first JSON object from a vision-model response."""
    try:
        m = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return dict(_EMPTY_CELL)


def extract_data_cell(client, img: Image.Image,
                       floor: str, column: str) -> dict:
    """Send a single data cell image to GPT-4o and return structured data."""
    prompt = _DATA_PROMPT_TEMPLATE.format(floor=floor, column=column)
    return _parse_json_response(call_vision(client, img, prompt, max_tokens=500))


def extract_combined_row(client, img: Image.Image, floor: str) -> dict:
    """
    Read the FULL WIDTH of a data row as one combined cell.

    Called when individual column cells all return empty data, indicating
    the row contains a single shared specification for every column
    (e.g. 'SIZE AS PER PLAN', a single large structural drawing, etc.).
    """
    prompt = _COMBINED_PROMPT_TEMPLATE.format(floor=floor)
    return _parse_json_response(call_vision(client, img, prompt, max_tokens=500))


def _is_empty(cell_data: dict) -> bool:
    """Return True if every field in a cell dict is '---' or missing."""
    return all(cell_data.get(k, "---") == "---" for k in _EMPTY_CELL)


# ══════════════════════════════════════════════════════════════════════════════
# §6  Output JSON Parsers
# ══════════════════════════════════════════════════════════════════════════════

def parse_size(raw: str) -> dict:
    """
    "230 X 600"        → {"width": 230, "depth": None, "length": 600}
    "230 X 600 X 800"  → {"width": 230, "depth": 600,  "length": 800}
    "L SHAPE" / "---" / "AS PER PLAN"  → {"width": None, "depth": None, "length": None}
    """
    empty = {"width": None, "depth": None, "length": None}
    if not raw or raw.strip() in ("---", "", "N/A", "n/a"):
        return empty

    parts = re.split(r'\s*[xX×]\s*', raw.strip())
    nums  = []
    for p in parts:
        try:
            nums.append(int(float(p.strip())))
        except ValueError:
            pass

    if len(nums) == 2:
        return {"width": nums[0], "depth": None, "length": nums[1]}
    if len(nums) >= 3:
        return {"width": nums[0], "depth": nums[1], "length": nums[2]}
    return empty


def parse_reinforcement(raw: str) -> list:
    """
    "6-20 TOR + 4-16 TOR"  → ["6-20T", "4-16T"]
    "49-25 TOR"             → ["49-25T"]
    "---"                   → []
    """
    if not raw or raw.strip() in ("---", ""):
        return []

    result = []
    for part in re.split(r'\s*\+\s*', raw.strip()):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(\d+)\s*[-–]\s*(\d+)\s*(?:TOR|T)\b', part, re.IGNORECASE)
        if m:
            result.append(f"{m.group(1)}-{m.group(2)}T")
        else:
            nums = re.findall(r'\d+', part)
            if len(nums) >= 2:
                result.append(f"{nums[0]}-{nums[1]}T")
    return result


def parse_stirrups(raw: str) -> dict:
    """
    "10 TOR 15 @ 75 + @ 115 + 15 @ 75 C/C"
        → {"dia": ["10-T15"], "spacing": ["75 C/C", "115 C/C"]}
    "---" → {"dia": [], "spacing": []}
    """
    empty: dict = {"dia": [], "spacing": []}
    if not raw or raw.strip() in ("---", ""):
        return empty

    dias = [
        f"{m[0]}-T{m[1]}"
        for m in re.findall(r'(\d+)\s*(?:TOR|T)\s*(\d+)', raw, re.IGNORECASE)
    ]

    seen: set     = set()
    spacings: list = []

    for n in re.findall(r'(\d+)\s*C/C', raw, re.IGNORECASE):
        if n not in seen:
            spacings.append(f"{n} C/C")
            seen.add(n)
    for n in re.findall(r'@\s*(\d+)', raw):
        if n not in seen:
            spacings.append(f"{n} C/C")
            seen.add(n)

    return {"dia": dias, "spacing": spacings}


def _build_entry(clabel: str, flabel: str, raw: dict, _to_key) -> dict:
    """Convert raw vision-API dict into the final flat JSON entry."""
    mix_raw = raw.get("CONC_MIX", "---")
    return {
        "column_no":     clabel,
        "column_name":   _to_key(flabel),
        "size":          parse_size(raw.get("SIZE", "---")),
        "reinforcement": parse_reinforcement(raw.get("VERT_REINF", "---")),
        "stirrups":      parse_stirrups(raw.get("RING", "---")),
        "mix":           mix_raw if mix_raw and mix_raw != "---" else None,
        "steel_grade":   None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# §7  Debug Overlay  (optional)
# ══════════════════════════════════════════════════════════════════════════════

def save_debug_overlay(img:         Image.Image,
                        h_grid:      list,
                        v_grid:      list,
                        header_band: tuple,
                        label_band:  tuple,
                        out_path:    str) -> None:
    """Save a colour-coded grid-overlay PNG for inspection."""
    W, H    = img.size
    rgba    = img.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr      = ImageDraw.Draw(overlay)

    for y in h_grid:
        dr.line([(0, y), (W, y)], fill=(0, 220, 0, 200), width=3)
    for x in v_grid:
        dr.line([(x, 0), (x, H)], fill=(30, 120, 255, 200), width=3)

    hy0, hy1 = header_band
    dr.rectangle([0, hy0, W, hy1], fill=(255, 220, 0, 60))

    lx0, lx1 = label_band
    dr.rectangle([lx0, 0, lx1, H], fill=(255, 110, 0, 60))

    Image.alpha_composite(rgba, overlay).convert("RGB").save(out_path)
    print(f"       Debug overlay → {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# §8  Main Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def extract_column_schedule(pdf_path:    str,
                             output_path: str  = "output.json",
                             dpi:         int  = 600,
                             upscale:     int  = 2,
                             debug:       bool = False) -> dict:
    """
    Fully generalized extraction pipeline for structural-engineering
    column schedule PDFs.

    No hard-coded coordinates — all grid boundaries are discovered
    automatically from the image.

    Parameters
    ----------
    pdf_path    : Path to the input PDF.
    output_path : Where to write the JSON result.
    dpi         : PDF render resolution.  600 is the recommended minimum.
    upscale     : Per-cell upscaling factor for better OCR (2 = default).
    debug       : If True, save a colour-coded grid-overlay PNG alongside
                  the JSON output.

    Returns
    -------
    The extracted data as a Python dict (also written to `output_path`).
    """
    client = get_client()

    # ── Step 1 : Render ───────────────────────────────────────────────────────
    print(f"\n[1/6] Rendering '{Path(pdf_path).name}' at {dpi} DPI …")
    img  = pdf_to_image(pdf_path, dpi=dpi)
    W, H = img.size
    print(f"       Image: {W} × {H} px")

    # ── Steps 2+3 : Adaptive horizontal line detection → data-row grid ────────
    #
    # WHY ADAPTIVE?
    # Each floor cell has internal sub-row dividers (SIZE / CONC.MIX / VERT.REINF
    # / RING).  On narrow PDFs these span ~28 % width → not detected at h_thr=0.15.
    # On wide PDFs (11 columns) they span ~55 % → ARE detected, flooding h_all.
    # Increasing the threshold progressively filters sub-rows (lighter) while
    # keeping the thicker floor-boundary lines.
    print("[2/6] Detecting horizontal grid lines (adaptive threshold) …")

    H_THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    min_floor_h  = H / 25
    max_floor_h  = H / 2

    h_all      = []
    h_grid     = []
    chosen_thr = H_THRESHOLDS[0]

    for h_thr in H_THRESHOLDS:
        h_raw, _ = detect_lines(img, h_thr=h_thr, v_thr=0.0, min_dist=30)
        cand_all  = cluster_lines(sorted(h_raw))

        if len(cand_all) < 4:
            h_all = cand_all
            continue

        cand_grid = find_best_grid(cand_all, min_rows=3, tolerance=0.35)
        n_cand    = len(cand_grid) - 1

        if n_cand < 3:
            h_all  = cand_all
            h_grid = cand_grid
            continue

        avg_h      = (cand_grid[-1] - cand_grid[0]) / n_cand
        h_all      = cand_all
        h_grid     = cand_grid
        chosen_thr = h_thr

        if min_floor_h <= avg_h <= max_floor_h:
            break

    print(f"       {len(h_all)} horizontal lines  (h_thr={chosen_thr})")

    # ── Step 3 : Report ───────────────────────────────────────────────────────
    print("[3/6] Identifying data rows …")

    if len(h_grid) < 3:
        raise ValueError(
            f"Only {len(h_grid)} consistent horizontal grid lines found "
            f"(need ≥ 3 for at least 2 data rows). "
            "Use --debug to inspect the detected lines."
        )

    n_rows = len(h_grid) - 1
    print(f"       {n_rows} data rows  |  Y: {h_grid[0]} – {h_grid[-1]} px")

    # ── Step 4 : Detect vertical lines scoped to the table's Y range ──────────
    print("[4/6] Detecting vertical grid lines within table …")
    _, v_raw = detect_lines(
        img,
        y0=h_grid[0], y1=h_grid[-1],
        h_thr=0.0, v_thr=0.20, min_dist=30,
    )
    v_all  = cluster_lines(sorted(v_raw))
    v_core = find_regular_grid(v_all, tolerance=0.40)
    v_grid = extend_grid_with_trailing_lines(v_core, v_all, max_extra_multiplier=4.0)
    print(f"       {len(v_all)} vertical lines after clustering")

    if len(v_grid) < 3:
        raise ValueError(
            f"Only {len(v_grid)} consistent vertical grid lines found "
            f"(need ≥ 3 for label col + ≥ 1 data col). "
            "Try --debug or adjust thresholds."
        )

    # ── Step 5 : Identify the label column and column-header row ──────────────
    typical_col_width   = float(np.median(
        [v_grid[i + 1] - v_grid[i] for i in range(len(v_grid) - 1)]
    ))
    label_col_threshold = int(typical_col_width * 0.5)

    if v_grid[0] <= label_col_threshold:
        label_x0   = v_grid[0]
        label_x1   = v_grid[1]
        data_col_x = v_grid[1:]
    else:
        label_x0   = max(0, v_grid[0] - int(typical_col_width * 0.9))
        label_x1   = v_grid[0]
        data_col_x = v_grid

    n_cols = len(data_col_x) - 1
    if n_cols < 1:
        raise ValueError("No data columns detected. Use --debug to inspect.")

    print(f"       {n_cols} data columns  |  X: {data_col_x[0]} – {data_col_x[-1]} px")

    above_h = [y for y in h_all if y < h_grid[0]]
    below_h = [y for y in h_all if y > h_grid[-1]]

    header_y0 = above_h[-1] if above_h else max(0, h_grid[0] - (h_grid[1] - h_grid[0]) // 2)
    header_y1 = h_grid[0]

    footer_y0 = h_grid[-1] if below_h else None
    footer_y1 = below_h[0] if below_h else None

    print(f"       Column-header row  Y: {header_y0} – {header_y1} px")
    if footer_y0 is not None:
        print(f"       Column-footer row  Y: {footer_y0} – {footer_y1} px")
    print(f"       Floor-label column X: {label_x0} – {label_x1} px")

    if debug:
        dbg_path = str(Path(output_path).with_suffix(".debug.png"))
        save_debug_overlay(
            img, h_grid, v_grid,
            (header_y0, header_y1),
            (label_x0,  label_x1),
            dbg_path,
        )

    # ── Step 5 : Extract labels ───────────────────────────────────────────────
    print("[5/6] Extracting column and floor labels via Vision API …")

    col_labels     = extract_col_labels(client, img, header_y0, header_y1, data_col_x, upscale)
    fallback_count = sum(1 for lbl in col_labels if lbl.startswith("COL_"))

    # Fallback A: try footer row below the grid (4.pdf-style)
    if fallback_count > len(col_labels) // 2 and footer_y0 is not None:
        print(f"       Header row gave {fallback_count} fallback(s) — trying footer row …")
        footer_labels    = extract_col_labels(client, img, footer_y0, footer_y1, data_col_x, upscale)
        footer_fallbacks = sum(1 for lbl in footer_labels if lbl.startswith("COL_"))
        if footer_fallbacks < fallback_count:
            print(f"       Footer row is better ({footer_fallbacks} fallback(s)) — using it.")
            col_labels     = footer_labels
            fallback_count = footer_fallbacks
        else:
            print(f"       Footer row also gave fallbacks — keeping header row result.")

    # Extract floor labels (may include SKIP markers for non-floor rows)
    raw_floor_labels = extract_floor_labels(
        client, img, label_x0, label_x1, h_grid, upscale
    )

    print("       Validating floor rows …")
    row_bounds, floor_labels = filter_and_recheck_floor_rows(
        client, img, label_x0, label_x1, h_grid, raw_floor_labels, upscale
    )

    n_rows = len(row_bounds)
    if n_rows == 0:
        raise ValueError("No valid floor rows found after filtering. Use --debug to inspect.")

    # Fallback B: try SKIP rows at the bottom of the grid (2.pdf-style)
    # Some PDFs place column labels inside a thin row that is correctly
    # labelled SKIP (not a floor row) but is not in below_h either.
    fallback_count = sum(1 for lbl in col_labels if lbl.startswith("COL_"))
    if fallback_count > len(col_labels) // 2 and row_bounds:
        last_valid_y1  = row_bounds[-1][1]
        skipped_bottom = [
            (h_grid[i], h_grid[i + 1])
            for i, lbl in enumerate(raw_floor_labels)
            if lbl == "SKIP" and h_grid[i] >= last_valid_y1 - 50
        ]
        for sy0, sy1 in skipped_bottom:
            print(
                f"       Still {fallback_count} COL_N fallback(s) — "
                f"trying SKIP row ({sy0}–{sy1} px) as column labels …"
            )
            skip_labels    = extract_col_labels(client, img, sy0, sy1, data_col_x, upscale)
            skip_fallbacks = sum(1 for lbl in skip_labels if lbl.startswith("COL_"))
            if skip_fallbacks < fallback_count:
                print(f"       SKIP row is better ({skip_fallbacks} fallback(s)) — using it.")
                col_labels     = skip_labels
                fallback_count = skip_fallbacks
                break
            else:
                print(f"       SKIP row also gave fallbacks — continuing search.")

    # Pad / trim column labels to match detected column count
    col_labels += [f"COL_{i + 1}" for i in range(len(col_labels), n_cols)]
    col_labels  = col_labels[:n_cols]

    print(f"       Columns ({n_cols}): {col_labels}")
    print(f"       Floors  ({n_rows}): {floor_labels}")

    def _to_key(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")

    # ── Step 6 : Extract every data cell ─────────────────────────────────────
    total = n_rows * n_cols
    print(f"[6/6] Extracting {total} data cells via Vision API …")

    result = {
        "document": Path(pdf_path).name,
        "title":    "Column Schedule",
        "columns":  [],
    }

    done = 0
    for r in range(n_rows):
        y0, y1 = row_bounds[r]
        flabel = floor_labels[r]

        # ── Extract each column cell individually ────────────────────────────
        row_cells: list[dict] = []
        for c in range(n_cols):
            x0, x1 = data_col_x[c], data_col_x[c + 1]
            clabel  = col_labels[c]
            cell    = _crop_upscale(img, x0, y0, x1, y1, upscale)
            done   += 1
            print(
                f"       [{done:>3}/{total}]  {flabel} / {clabel} …",
                end=" ", flush=True,
            )
            raw = extract_data_cell(client, cell, flabel, clabel)
            print("✓")
            row_cells.append(raw)

        # ── Combined-row detection ───────────────────────────────────────────
        # If >50 % of individual cells returned all-empty data, the row most
        # likely contains ONE shared specification drawn across the full width
        # (e.g. "SIZE AS PER PLAN" with a single large structural diagram).
        # Re-read the entire row width as a single image and broadcast.
        empty_count = sum(1 for d in row_cells if _is_empty(d))
        if empty_count > n_cols // 2:
            print(
                f"       ↳ {empty_count}/{n_cols} cells empty — "
                f"row appears to be COMBINED; re-reading full width …",
                end=" ", flush=True,
            )
            full_row  = _crop_upscale(img, data_col_x[0], y0, data_col_x[-1], y1, upscale)
            combined  = extract_combined_row(client, full_row, flabel)
            print("✓")
            if not _is_empty(combined):
                print(f"       ↳ Combined read succeeded — broadcasting to all {n_cols} columns.")
                row_cells = [combined] * n_cols
            else:
                print(f"       ↳ Combined read also returned empty — keeping individual results.")

        # ── Build output entries ─────────────────────────────────────────────
        for c, raw in enumerate(row_cells):
            result["columns"].append(
                _build_entry(col_labels[c], flabel, raw, _to_key)
            )

    # ── Save ──────────────────────────────────────────────────────────────────
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n✅  Done!  {total} cells extracted → {output_path}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=(
            "Generalized column schedule PDF extractor — "
            "no hard-coded coordinates, works for any floor/column count."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--pdf",     required=True,
                    help="Path to the input PDF file")
    ap.add_argument("--output",  default="output.json",
                    help="Output JSON file path")
    ap.add_argument("--dpi",     type=int, default=600,
                    help="PDF render resolution (600 recommended minimum)")
    ap.add_argument("--upscale", type=int, default=2,
                    help="Per-cell upscale factor (higher = better OCR, slower)")
    ap.add_argument("--debug",   action="store_true",
                    help="Save a colour-coded grid-overlay PNG for inspection")
    args = ap.parse_args()

    extract_column_schedule(
        pdf_path    = args.pdf,
        output_path = args.output,
        dpi         = args.dpi,
        upscale     = args.upscale,
        debug       = args.debug,
    )