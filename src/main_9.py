# import os
# import re
# import json
# from tqdm import tqdm
# from collections import defaultdict

# from config import INPUT_DIR, OUTPUT_DIR
# from pdf_to_images import convert_pdf_to_images
# from vision_extractor import extract_from_image


# def load_prompt():
#     with open(
#         os.path.join(os.path.dirname(__file__), "prompt_9.txt"),
#         "r",
#         encoding="utf-8"
#     ) as f:
#         return f.read()


# # ─────────────────────────────────────────────
# # Reinforcement FIXES (UPDATED)
# # ─────────────────────────────────────────────

# def normalize_reinforcement_entry(r):
#     r = str(r).strip()

#     # Remove leading +
#     r = re.sub(r'^\++', '', r)

#     # Convert Tor → T
#     r = re.sub(r'\b[Tt][Oo][Rr]\b', 'T', r)

#     # Remove spaces
#     r = re.sub(r'\s+', '', r)

#     # Ensure valid format
#     match = re.match(r'(\d+)-(\d+)T', r)
#     if match:
#         return f"{int(match.group(1))}-{int(match.group(2))}T"

#     return None


# def normalize_reinforcement(reinforcement):
#     if not reinforcement or not isinstance(reinforcement, list):
#         return []

#     result = []
#     for r in reinforcement:
#         n = normalize_reinforcement_entry(r)
#         if n and n not in result:
#             result.append(n)

#     return result


# def validate_reinforcement_set(reinf_list):
#     cleaned = []

#     for r in reinf_list:
#         match = re.match(r'(\d+)-(\d+)T', r)
#         if not match:
#             continue

#         count, dia = int(match.group(1)), int(match.group(2))

#         # Reject unrealistic values
#         if dia > 40 or count > 40:
#             continue

#         cleaned.append((count, dia, r))

#     # Remove diameter outliers
#     if len(cleaned) >= 2:
#         dias = [d for _, d, _ in cleaned]
#         if max(dias) - min(dias) > 12:
#             median = sorted(dias)[len(dias)//2]
#             cleaned = [x for x in cleaned if abs(x[1] - median) <= 8]

#     return [r for _, _, r in cleaned]


# def remove_near_duplicates(reinf):
#     return list(dict.fromkeys(reinf))[:3]


# # ─────────────────────────────────────────────
# # Existing Normalization (UNCHANGED)
# # ─────────────────────────────────────────────

# def normalize_spacing_entry(s):
#     s = str(s).strip().upper()
#     s = re.sub(r'^@\s*', '', s)
#     s = s.replace(' ', '')
#     if re.match(r'^\d+$', s):
#         return f"{s} C/C"
#     s = re.sub(r'C/C$', ' C/C', s).strip()
#     return s if s else None


# def normalize_stirrups(stirrups):
#     if not stirrups or not isinstance(stirrups, dict):
#         return {"dia": [], "spacing": []}

#     raw_dia = stirrups.get("dia", [])
#     raw_spacing = stirrups.get("spacing", [])

#     if not isinstance(raw_dia, list):
#         raw_dia = [raw_dia]
#     if not isinstance(raw_spacing, list):
#         raw_spacing = [raw_spacing]

#     dia = []
#     for d in raw_dia:
#         d = str(d).strip()
#         d = re.sub(r'\s*[Tt][Oo][Rr]\s*', 'T', d).replace(' ', '')
#         if d and d not in dia:
#             dia.append(d)

#     spacing = []
#     for s in raw_spacing:
#         s_str = str(s).strip()
#         if re.search(r'[Rr]ing', s_str):
#             continue
#         n = normalize_spacing_entry(s_str)
#         if n and n not in spacing:
#             spacing.append(n)

#     return {"dia": dia, "spacing": spacing}


# def normalize_size(size):
#     if isinstance(size, str):
#         nums = [int(n) for n in re.findall(r'\d+', size)]
#         if len(nums) == 2:
#             return {"width": nums[0], "depth": None, "length": nums[1]}
#         elif len(nums) == 3:
#             return {"width": nums[0], "depth": nums[1], "length": nums[2]}
#         return {"width": None, "depth": None, "length": None}

#     if isinstance(size, dict):
#         def safe_int(v):
#             if v is None:
#                 return None
#             try:
#                 return int(v)
#             except:
#                 return None

#         return {
#             "width": safe_int(size.get("width")),
#             "depth": safe_int(size.get("depth")),
#             "length": safe_int(size.get("length")),
#         }

#     return {"width": None, "depth": None, "length": None}


# def normalize_column_no(col_no):
#     col_no = str(col_no).strip().upper()
#     parts = [p.strip() for p in re.split(r'[,;]+', col_no)]
#     parts = [p for p in parts if p]
#     return ", ".join(parts)


# def normalize_column_name(name):
#     return str(name).strip().upper() if name else name


# def normalize_mix(mix):
#     return str(mix).strip().upper() if mix else None


# # ─────────────────────────────────────────────
# # Validation
# # ─────────────────────────────────────────────

# def is_valid_column(col):
#     col_no = str(col.get("column_no", "")).strip().upper()
#     if not col_no:
#         return False
#     parts = [p.strip() for p in re.split(r'[,;]+', col_no)]
#     return any(p.startswith("AC") for p in parts)


# def post_process(columns):
#     processed = []

#     for col in columns:
#         if not isinstance(col, dict):
#             continue
#         if not is_valid_column(col):
#             continue

#         col["column_no"]   = normalize_column_no(col.get("column_no", ""))
#         col["column_name"] = normalize_column_name(col.get("column_name", ""))
#         col["size"]        = normalize_size(col.get("size"))
        
#         # ✅ FIXED PIPELINE
#         reinf = normalize_reinforcement(col.get("reinforcement", []))
#         reinf = validate_reinforcement_set(reinf)
#         reinf = remove_near_duplicates(reinf)
#         col["reinforcement"] = reinf

#         col["stirrups"]    = normalize_stirrups(col.get("stirrups"))
#         col["mix"]         = normalize_mix(col.get("mix"))
#         col["steel_grade"] = col.get("steel_grade", None)

#         processed.append(col)

#     return processed


# # ─────────────────────────────────────────────
# # Merge + Report (UNCHANGED)
# # ─────────────────────────────────────────────

# def merge_pages(all_columns):
#     seen = {}
#     for col in all_columns:
#         key = (col["column_no"], col["column_name"])
#         seen[key] = col
#     return list(seen.values())


# def report_completeness(columns):
#     groups = defaultdict(set)
#     all_laps = set()

#     for col in columns:
#         groups[col["column_no"]].add(col["column_name"])
#         all_laps.add(col["column_name"])

#     n_groups = len(groups)
#     n_laps = len(all_laps)

#     print(f"\n📊 Extraction summary:")
#     print(f"   Groups : {n_groups} | LAP bands : {n_laps}")
#     print(f"   Expected : {n_groups * n_laps} | Actual : {len(columns)}\n")


# # ─────────────────────────────────────────────
# # JSON parsing (UNCHANGED)
# # ─────────────────────────────────────────────

# def parse_model_output(raw):
#     text = raw.strip()

#     if '```' in text:
#         parts = text.split('```')
#         if len(parts) >= 3:
#             block = parts[1]
#             if block.lower().startswith('json'):
#                 block = block[4:]
#             text = block.strip()

#     brace_idx = text.find('{')
#     if brace_idx > 0:
#         text = text[brace_idx:]

#     return json.loads(text)


# # ─────────────────────────────────────────────
# # MAIN
# # ─────────────────────────────────────────────

# def process_pdf(pdf_path):
#     file_name = os.path.splitext(os.path.basename(pdf_path))[0]
#     output_folder = os.path.join(OUTPUT_DIR, file_name)
#     os.makedirs(output_folder, exist_ok=True)

#     image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=300)
#     prompt = load_prompt()
#     all_columns = []

#     for img_path in tqdm(image_paths, desc=f"Processing {file_name}"):
#         print(f"🔍 Processing: {img_path}")

#         result = extract_from_image(img_path, prompt)

#         try:
#             parsed = parse_model_output(result)
#             raw_columns = parsed.get("columns", [])

#             processed = post_process(raw_columns)
#             all_columns.extend(processed)

#         except Exception as e:
#             print(f"⚠️ Failed parsing: {e}")

#     final_columns = merge_pages(all_columns)
#     report_completeness(final_columns)

#     output_file = os.path.join(output_folder, f"{file_name}.json")
#     with open(output_file, "w", encoding="utf-8") as f:
#         json.dump({"columns": final_columns}, f, indent=2, ensure_ascii=False)

#     print(f"✅ Saved → {output_file}")


# def main():
#     os.makedirs(OUTPUT_DIR, exist_ok=True)

#     pdf_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".pdf")]

#     for pdf in pdf_files:
#         process_pdf(os.path.join(INPUT_DIR, pdf))


# if __name__ == "__main__":
#     main()



# """
# main_9.py  –  Pattern 9 column schedule extractor
# ===================================================
# Uses PyMuPDF to extract text with spatial coordinates directly from the PDF,
# then assigns each word to its (group_row x lap_column) cell based on XY
# position.  This eliminates vision-model hallucinations entirely.

# Layout for Pattern 9:
#   - 5 LAP-band columns left to right  (X centres ~269, 710, 1151, 1592, 2033)
#   - 15 group rows top to bottom

# Fallback: if a page has no extractable text (scanned image), the vision model
# is used for that page only.
# """

# import os
# import re
# import json
# from tqdm import tqdm
# from collections import defaultdict

# import fitz  # PyMuPDF  (pip install pymupdf)

# from config import INPUT_DIR, OUTPUT_DIR
# from pdf_to_images import convert_pdf_to_images
# from vision_extractor import extract_from_image


# # ─────────────────────────────────────────────────────────────────────────────
# # Layout constants (calibrated from the actual PDF coordinate space)
# # ─────────────────────────────────────────────────────────────────────────────

# LAP_X_CENTERS = [269, 710, 1151, 1592, 2033]
# LAP_X_HALF    = 210          # x-tolerance (half column width)

# LAP_NAMES = [
#     "5th LAP TO 6th LAP",
#     "6th LAP TO 7th LAP",
#     "7th LAP TO 8th LAP",
#     "8th LAP TO 9th LAP",
#     "9th LAP TO 10th LAP",
# ]

# GROUP_Y_CENTERS = [
#     464,   # GROUP 1  – AC13, AC14
#     643,   # GROUP 2  – AC06, AC07
#     822,   # GROUP 3  – AC02, AC03
#     1001,  # GROUP 4  – AC17, AC22, AC23, AC24, AC25
#     1180,  # GROUP 5  – AC29
#     1360,  # GROUP 6  – AC08, AC26
#     1539,  # GROUP 7  – AC05
#     1718,  # GROUP 8  – AC27, AC28
#     1897,  # GROUP 9  – AC10, AC11
#     2076,  # GROUP 10 – AC01, AC04
#     2256,  # GROUP 11 – AC09, AC12
#     2435,  # GROUP 12 – AC21
#     2614,  # GROUP 13 – AC15, AC18
#     2793,  # GROUP 14 – AC16, AC19
#     2972,  # GROUP 15 – AC20
# ]
# GROUP_Y_HALF = 90            # y-tolerance (half row height)

# GROUP_LABELS = [
#     "AC13, AC14",
#     "AC06, AC07",
#     "AC02, AC03",
#     "AC17, AC22, AC23, AC24, AC25",
#     "AC29",
#     "AC08, AC26",
#     "AC05",
#     "AC27, AC28",
#     "AC10, AC11",
#     "AC01, AC04",
#     "AC09, AC12",
#     "AC21",
#     "AC15, AC18",
#     "AC16, AC19",
#     "AC20",
# ]


# # ─────────────────────────────────────────────────────────────────────────────
# # Spatial helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def find_lap_col(x: float) -> int:
#     best_i, best_d = -1, float("inf")
#     for i, cx in enumerate(LAP_X_CENTERS):
#         d = abs(x - cx)
#         if d < best_d:
#             best_d, best_i = d, i
#     return best_i if best_d <= LAP_X_HALF else -1


# def find_group_row(y: float) -> int:
#     best_i, best_d = -1, float("inf")
#     for i, cy in enumerate(GROUP_Y_CENTERS):
#         d = abs(y - cy)
#         if d < best_d:
#             best_d, best_i = d, i
#     return best_i if best_d <= GROUP_Y_HALF else -1


# def parse_size(s: str) -> dict:
#     nums = [int(n) for n in re.findall(r"\d+", s)]
#     if len(nums) == 2:
#         return {"width": nums[0], "depth": None,    "length": nums[1]}
#     if len(nums) == 3:
#         return {"width": nums[0], "depth": nums[1], "length": nums[2]}
#     return {"width": None, "depth": None, "length": None}


# # ─────────────────────────────────────────────────────────────────────────────
# # Core extractor – direct PDF text + coordinates
# # ─────────────────────────────────────────────────────────────────────────────

# def extract_from_pdf_page(page) -> list:
#     """
#     Parse a fitz page object and return a list of column-schedule records.
#     Returns an empty list if the page has no usable text (scanned).
#     """
#     words = page.get_text("words")  # list of (x0,y0,x1,y1,text,block,line,word)
#     if not words:
#         return []

#     n_g = len(GROUP_LABELS)
#     n_l = len(LAP_NAMES)

#     # Per-cell accumulators
#     reinf   = {(g, l): [] for g in range(n_g) for l in range(n_l)}
#     sizes   = {}
#     dia_acc = {(g, l): [] for g in range(n_g) for l in range(n_l)}
#     sp_acc  = {(g, l): [] for g in range(n_g) for l in range(n_l)}
#     mix_acc = {}

#     for entry in words:
#         x0, y0, x1, y1, txt = entry[0], entry[1], entry[2], entry[3], entry[4]
#         x = (x0 + x1) / 2
#         y = (y0 + y1) / 2
#         li = find_lap_col(x)
#         gi = find_group_row(y)
#         if li < 0 or gi < 0:
#             continue
#         key = (gi, li)

#         # Reinforcement: "12-16" or "+4-12"
#         if re.match(r"^\+?\d+-\d+$", txt):
#             clean = re.sub(r"^\+", "", txt) + "T"
#             if clean not in reinf[key]:
#                 reinf[key].append(clean)

#         # Size: "300x950" or "300x500x950"
#         elif re.match(r"^\d+x\d+", txt):
#             sizes[key] = txt

#         # Stirrup bar designation: "8T"
#         elif re.match(r"^\d+T$", txt):
#             if txt not in dia_acc[key]:
#                 dia_acc[key].append(txt)

#         # Stirrup spacing value
#         elif txt in ("100", "150"):
#             sp = f"{txt} C/C"
#             if sp not in sp_acc[key]:
#                 sp_acc[key].append(sp)

#         # Concrete mix
#         elif re.match(r"^M\d+$", txt):
#             mix_acc[key] = txt

#     records = []
#     for gi, grp in enumerate(GROUP_LABELS):
#         for li, lap in enumerate(LAP_NAMES):
#             key = (gi, li)
#             if key not in sizes:
#                 continue   # no data for this cell
#             records.append({
#                 "column_no":    grp,
#                 "column_name":  lap,
#                 "size":         parse_size(sizes[key]),
#                 "reinforcement": reinf[key],
#                 "stirrups": {
#                     "dia":     dia_acc[key],
#                     "spacing": sorted(sp_acc[key]),
#                 },
#                 "mix":          mix_acc.get(key),
#                 "steel_grade":  None,
#             })
#     return records


# # ─────────────────────────────────────────────────────────────────────────────
# # Vision-model fallback (scanned pages only)
# # ─────────────────────────────────────────────────────────────────────────────

# def load_prompt():
#     with open(
#         os.path.join(os.path.dirname(__file__), "prompt_9.txt"),
#         "r", encoding="utf-8",
#     ) as f:
#         return f.read()


# def parse_model_output(raw: str) -> dict:
#     text = raw.strip()
#     if "```" in text:
#         parts = text.split("```")
#         if len(parts) >= 3:
#             block = parts[1]
#             if block.lower().startswith("json"):
#                 block = block[4:]
#             text = block.strip()
#     brace = text.find("{")
#     if brace > 0:
#         text = text[brace:]
#     return json.loads(text)


# def _norm_reinf_entry(r):
#     r = re.sub(r"^\++", "", str(r).strip())
#     r = re.sub(r"\s*[Tt][Oo][Rr]\s*$", "T", r)
#     r = r.replace(" ", "")
#     m = re.match(r"^(\d+)-(\d+)T$", r)
#     return f"{int(m.group(1))}-{int(m.group(2))}T" if m else None


# def _norm_reinf(lst):
#     seen = []
#     for r in (lst or []):
#         n = _norm_reinf_entry(r)
#         if n and n not in seen and int(n.split("-")[0]) <= 40 and int(n.split("-")[1][:-1]) <= 40:
#             seen.append(n)
#     return seen


# def _norm_stirrups(s):
#     if not s or not isinstance(s, dict):
#         return {"dia": [], "spacing": []}
#     dia = [re.sub(r"\s*[Tt][Oo][Rr]\s*", "T", str(d).strip()).replace(" ", "")
#            for d in (s.get("dia", []) if isinstance(s.get("dia"), list) else [s.get("dia")])]
#     dia = list(dict.fromkeys(filter(None, dia)))
#     sp_out = []
#     for sp in (s.get("spacing", []) if isinstance(s.get("spacing"), list) else [s.get("spacing")]):
#         sp = str(sp).strip()
#         if re.search(r"[Rr]ing", sp):
#             continue
#         sp = re.sub(r"^@\s*", "", sp.upper()).replace(" ", "")
#         sp = f"{sp} C/C" if re.match(r"^\d+$", sp) else re.sub(r"C/C$", " C/C", sp).strip()
#         if sp and sp not in sp_out:
#             sp_out.append(sp)
#     return {"dia": dia, "spacing": sp_out}


# def post_process_vision(columns: list) -> list:
#     out = []
#     for col in columns:
#         if not isinstance(col, dict):
#             continue
#         col_no = str(col.get("column_no", "")).strip().upper()
#         parts  = [p.strip() for p in re.split(r"[,;]+", col_no)]
#         if not any(p.startswith("AC") for p in parts):
#             continue
#         col["column_no"]   = ", ".join(parts)
#         col["column_name"] = str(col.get("column_name", "")).strip()
#         col["size"]        = parse_size(str(col.get("size", ""))) if isinstance(col.get("size"), str) \
#                              else {k: (int(v) if v else None) for k, v in (col.get("size") or {}).items()}
#         col["reinforcement"] = _norm_reinf(col.get("reinforcement", []))
#         col["stirrups"]    = _norm_stirrups(col.get("stirrups"))
#         col["mix"]         = str(col.get("mix", "")).strip().upper() or None
#         col["steel_grade"] = col.get("steel_grade")
#         out.append(col)
#     return out


# # ─────────────────────────────────────────────────────────────────────────────
# # Merge + sort + report
# # ─────────────────────────────────────────────────────────────────────────────

# def merge_pages(all_records: list) -> list:
#     seen = {}
#     for rec in all_records:
#         key = (rec["column_no"], rec["column_name"])
#         seen[key] = rec   # last page wins (dedup across pages)
#     return list(seen.values())


# def sort_records(records: list) -> list:
#     grp_order = {g: i for i, g in enumerate(GROUP_LABELS)}
#     lap_order = {l: i for i, l in enumerate(LAP_NAMES)}

#     def key(rec):
#         return (
#             grp_order.get(rec["column_no"], 99),
#             lap_order.get(rec["column_name"], 99),
#         )
#     return sorted(records, key=key)


# def report_completeness(records: list):
#     groups   = defaultdict(set)
#     all_laps = set()
#     for r in records:
#         groups[r["column_no"]].add(r["column_name"])
#         all_laps.add(r["column_name"])
#     print(f"\n📊 Extraction summary:")
#     print(f"   Groups : {len(groups)} | LAP bands : {len(all_laps)}")
#     print(f"   Expected : {len(groups) * len(all_laps)} | Actual : {len(records)}\n")
#     for grp, found in groups.items():
#         missing = all_laps - found
#         if missing:
#             print(f"   ⚠️  {grp} missing LAPs: {sorted(missing)}")


# # ─────────────────────────────────────────────────────────────────────────────
# # Main pipeline
# # ─────────────────────────────────────────────────────────────────────────────

# def process_pdf(pdf_path: str):
#     file_name     = os.path.splitext(os.path.basename(pdf_path))[0]
#     output_folder = os.path.join(OUTPUT_DIR, file_name)
#     os.makedirs(output_folder, exist_ok=True)

#     doc         = fitz.open(pdf_path)
#     all_records = []
#     prompt      = None   # load lazily

#     for page_no in tqdm(range(len(doc)), desc=f"Processing {file_name}"):
#         page    = doc[page_no]
#         records = extract_from_pdf_page(page)

#         if records:
#             print(f"  ✅ Page {page_no + 1}: {len(records)} records via PDF text")
#             all_records.extend(records)
#         else:
#             print(f"  ⚠️  Page {page_no + 1}: no text – trying vision model…")
#             if prompt is None:
#                 prompt = load_prompt()
#             image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=300)
#             if page_no < len(image_paths):
#                 raw = extract_from_image(image_paths[page_no], prompt)
#                 try:
#                     parsed  = parse_model_output(raw)
#                     vision  = post_process_vision(parsed.get("columns", []))
#                     all_records.extend(vision)
#                     print(f"     Vision: {len(vision)} records")
#                 except Exception as e:
#                     print(f"     ❌ Vision parse failed: {e}")
#                     print(f"        Raw (first 400): {raw[:400]}")

#     final = sort_records(merge_pages(all_records))
#     report_completeness(final)

#     output_file = os.path.join(output_folder, f"{file_name}.json")
#     with open(output_file, "w", encoding="utf-8") as f:
#         json.dump({"columns": final}, f, indent=2, ensure_ascii=False)
#     print(f"✅ Saved → {output_file}")


# def main():
#     os.makedirs(OUTPUT_DIR, exist_ok=True)
#     for pdf in [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".pdf")]:
#         process_pdf(os.path.join(INPUT_DIR, pdf))


# if __name__ == "__main__":
#     main()


"""
main_9_generalized.py — Generalized Column Schedule Extractor
=============================================================
Automatically detects the grid layout (number of LAP columns × group rows,
their X/Y coordinates, and their names) directly from the PDF content.

No hardcoded coordinates, no hardcoded labels — works with any PDF that
follows the same structural column-schedule format, regardless of how many
LAP bands or column groups it contains.

Detection Strategy
------------------
1.  Grid centres from SIZE text  (most reliable):
        SIZE tokens like "300x950" appear exactly once per cell.
        → Cluster their X positions  →  LAP column X centres
        → Cluster their Y positions  →  Group row Y centres

2.  LAP names  (matched by regex):
        Scan all lines for "Xth LAP TO Yth LAP" phrases and assign each
        phrase to its nearest detected X centre.

3.  Group labels  (matched by regex):
        Scan for "AC\\d+" tokens that appear in the left portion of the page,
        cluster them by Y, and assign each cluster to its nearest Y centre.

4.  Fallback generic names:
        If a position cannot be matched to real text, it gets a generic label
        ("LAP 1", "LAP 2", … / "GROUP 1", "GROUP 2", …).

5.  Vision-model fallback:
        Pages with no extractable text (scanned images) fall back to the
        vision model with a dynamically built prompt.
"""

import os
import re
import json
from tqdm import tqdm
from collections import defaultdict

import fitz  # PyMuPDF  (pip install pymupdf)

from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def ordinal_str(n: int) -> str:
    """Return English ordinal string: 1→'1st', 5→'5th', 11→'11th' …"""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def parse_size(s: str) -> dict:
    """Parse '300x950' → {width:300, depth:None, length:950}
            '300x500x950' → {width:300, depth:500, length:950}"""
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if len(nums) == 2:
        return {"width": nums[0], "depth": None,    "length": nums[1]}
    if len(nums) == 3:
        return {"width": nums[0], "depth": nums[1], "length": nums[2]}
    return {"width": None, "depth": None, "length": None}


def nearest_index(value: float, centers: list, half_width: float) -> int:
    """Return index of the nearest centre within half_width, else -1."""
    best_i, best_d = -1, float("inf")
    for i, c in enumerate(centers):
        d = abs(value - c)
        if d < best_d:
            best_d, best_i = d, i
    return best_i if best_d <= half_width else -1


def cluster_1d(positions: list) -> list:
    """
    Cluster a list of floats into groups separated by natural gaps.

    Algorithm: find the largest *ratio* jump in the sorted list of
    consecutive differences — that jump marks the boundary between
    intra-cluster noise and inter-cluster gaps.

    Returns a sorted list of cluster mean values.
    """
    if not positions:
        return []
    positions = sorted(positions)
    if len(positions) < 2:
        return [float(positions[0])]

    diffs = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    diffs_sorted = sorted(diffs)

    # Find the largest relative jump in sorted diffs.
    # That jump separates intra-cluster diffs from inter-cluster gaps.
    threshold = 20.0  # fallback minimum
    if len(diffs_sorted) >= 4:
        max_ratio, best_idx = 1.0, 0
        for i in range(len(diffs_sorted) - 1):
            if diffs_sorted[i] > 0:
                ratio = diffs_sorted[i + 1] / diffs_sorted[i]
                if ratio > max_ratio:
                    max_ratio, best_idx = ratio, i
        if max_ratio > 2.0:
            # Set threshold halfway between the two groups
            threshold = max((diffs_sorted[best_idx] + diffs_sorted[best_idx + 1]) / 2,
                            threshold)
    else:
        # Too few diffs — use simple heuristic
        threshold = max(sorted(diffs)[len(diffs) // 2] * 2, threshold)

    # Build clusters
    clusters = [[positions[0]]]
    for i, d in enumerate(diffs):
        if d < threshold:
            clusters[-1].append(positions[i + 1])
        else:
            clusters.append([positions[i + 1]])

    return [sum(c) / len(c) for c in clusters]


def _median_spacing(centers: list) -> float:
    """Return the median gap between consecutive sorted centres."""
    if len(centers) < 2:
        return 200.0
    diffs = sorted(centers[i + 1] - centers[i] for i in range(len(centers) - 1))
    return diffs[len(diffs) // 2]


# ─────────────────────────────────────────────────────────────────────────────
# Compiled regexes (shared across functions)
# ─────────────────────────────────────────────────────────────────────────────

_SIZE_RE  = re.compile(r"^\d+[xX]\d+")           # "300x950", "300x500x950"
_LAP_RE   = re.compile(                            # "5th LAP TO 6th LAP"
    r"(\d+)\s*(?:st|nd|rd|th)?\s*lap\s+to\s+(\d+)\s*(?:st|nd|rd|th)?\s*lap",
    re.IGNORECASE,
)
_AC_RE    = re.compile(r"^AC\d+$", re.IGNORECASE)  # "AC13", "AC14"
_MIX_RE   = re.compile(r"^M\d+$",  re.IGNORECASE)  # "M30", "M25"
_REINF_RE = re.compile(r"^\+?\d+-\d+T?$")          # "12-16", "+4-12", "12-16T"
_DIA_RE   = re.compile(r"^\d{1,2}T$")              # "8T", "10T"


# ─────────────────────────────────────────────────────────────────────────────
# Layout auto-detection
# ─────────────────────────────────────────────────────────────────────────────

def _get_all_words(doc: fitz.Document) -> list:
    """Collect (x0, y0, x1, y1, text) from every page."""
    out = []
    for page in doc:
        out.extend(
            (w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")
        )
    return out


def _detect_grid_centres(words: list):
    """
    Cluster the X and Y midpoints of every SIZE token to discover
    LAP-column X centres and group-row Y centres.
    Returns (x_centres, y_centres) — both sorted ascending.
    """
    xs, ys = [], []
    for x0, y0, x1, y1, txt in words:
        if _SIZE_RE.match(txt):
            xs.append((x0 + x1) / 2)
            ys.append((y0 + y1) / 2)
    return cluster_1d(xs), cluster_1d(ys)


def _detect_lap_names(words: list, x_centres: list, x_half: float) -> dict:
    """
    Reconstruct "Xth LAP TO Yth LAP" phrases from word-level tokens and
    assign each phrase to the nearest detected X centre.
    Returns {col_index: lap_name_string}.
    """
    # Group words into text lines (snap y to 8-pt grid)
    lines = defaultdict(list)
    for x0, y0, x1, y1, txt in words:
        cy = round((y0 + y1) / 2 / 8) * 8
        lines[cy].append((x0, x1, txt))

    phrase_positions = {}  # (n1, n2) → list of x-centre occurrences

    for cy, tokens in lines.items():
        tokens = sorted(tokens)
        line_text = " ".join(t[2] for t in tokens)

        for m in _LAP_RE.finditer(line_text):
            n1, n2 = int(m.group(1)), int(m.group(2))

            # Find the x-span of the matched tokens
            char_pos = 0
            x_mins, x_maxs = [], []
            for x0, x1, txt in tokens:
                idx = line_text.find(txt, char_pos)
                if idx < 0:
                    continue
                if idx < m.end() and (idx + len(txt)) > m.start():
                    x_mins.append(x0)
                    x_maxs.append(x1)
                char_pos = idx + len(txt)

            if x_mins:
                xc = (min(x_mins) + max(x_maxs)) / 2
                phrase_positions.setdefault((n1, n2), []).append(xc)

    # Average x positions across pages for each phrase
    phrase_avg = {key: sum(v) / len(v) for key, v in phrase_positions.items()}

    # Greedily assign each phrase to its nearest detected x_centre
    idx_to_name = {}
    used = set()
    for i, xc in enumerate(x_centres):
        best_key, best_d = None, float("inf")
        for key, px in phrase_avg.items():
            if key in used:
                continue
            d = abs(px - xc)
            if d < best_d and d <= x_half * 1.5:
                best_d, best_key = d, key
        if best_key:
            n1, n2 = best_key
            idx_to_name[i] = f"{ordinal_str(n1)} LAP TO {ordinal_str(n2)} LAP"
            used.add(best_key)

    return idx_to_name


def _detect_group_labels(words: list, y_centres: list, y_half: float) -> dict:
    """
    Collect "AC\\d+" tokens that appear in the left portion of the page,
    cluster them by Y position, and assign each cluster to the nearest
    detected Y centre.
    Returns {row_index: group_label_string}.
    """
    ac_items = []  # (y_centre, label_upper, x_centre)
    for x0, y0, x1, y1, txt in words:
        if _AC_RE.match(txt):
            ac_items.append(((y0 + y1) / 2, txt.upper(), (x0 + x1) / 2))

    if not ac_items:
        return {}

    # Filter to leftmost-x band: AC group labels share the smallest X values
    all_x = sorted(set(int(item[2]) for item in ac_items))
    if len(all_x) >= 4:
        # Lower 25% of x range
        x_threshold = all_x[0] + (all_x[-1] - all_x[0]) * 0.25
        left = [(y, lbl) for y, lbl, x in ac_items if x <= x_threshold]
    else:
        left = [(y, lbl) for y, lbl, x in ac_items]

    if not left:
        left = [(y, lbl) for y, lbl, x in ac_items]

    # Cluster these Y positions
    left.sort(key=lambda t: t[0])
    raw_cluster_centres = cluster_1d([y for y, _ in left])

    # Assign each AC label to its nearest raw cluster
    cluster_labels: dict[int, list] = defaultdict(list)
    for y, lbl in left:
        ci = nearest_index(y, raw_cluster_centres, y_half * 1.5)
        if ci >= 0:
            cluster_labels[ci].append(lbl)

    # Map raw cluster → detected y_centre → row index
    idx_to_name = {}
    for ci, yc in enumerate(raw_cluster_centres):
        ri = nearest_index(yc, y_centres, y_half * 1.5)
        if ri < 0 or ri in idx_to_name:
            continue
        labels = sorted(
            set(cluster_labels[ci]),
            key=lambda s: int(re.search(r"\d+", s).group()),
        )
        if labels:
            idx_to_name[ri] = ", ".join(labels)

    return idx_to_name


def detect_layout(doc: fitz.Document) -> dict:
    """
    Scan the entire document and return a fully-populated layout dict:

        lap_x_centres   : list[float]  — X positions of LAP columns (sorted)
        lap_names       : list[str]    — name for each LAP column
        lap_x_half      : float        — ½-width tolerance for X assignment
        group_y_centres : list[float]  — Y positions of group rows (sorted)
        group_labels    : list[str]    — label for each group row
        group_y_half    : float        — ½-height tolerance for Y assignment
    """
    words = _get_all_words(doc)

    # ── Step 1: grid centres from SIZE text ──────────────────────────────────
    lap_x, grp_y = _detect_grid_centres(words)

    if not lap_x or not grp_y:
        print("  ⚠️  No SIZE text found — PDF may be fully scanned (image-only).")
        return {}

    x_half = _median_spacing(lap_x) * 0.48
    y_half = _median_spacing(grp_y) * 0.48

    # ── Step 2: assign human-readable names ──────────────────────────────────
    lap_name_map   = _detect_lap_names  (words, lap_x, x_half)
    group_name_map = _detect_group_labels(words, grp_y, y_half)

    lap_names    = [lap_name_map  .get(i, f"LAP {i + 1}")   for i in range(len(lap_x))]
    group_labels = [group_name_map.get(i, f"GROUP {i + 1}") for i in range(len(grp_y))]

    return {
        "lap_x_centres":   lap_x,
        "lap_names":       lap_names,
        "lap_x_half":      x_half,
        "group_y_centres": grp_y,
        "group_labels":    group_labels,
        "group_y_half":    y_half,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direct PDF text extractor  (digital / selectable text pages)
# ─────────────────────────────────────────────────────────────────────────────

def extract_from_pdf_page(page: fitz.Page, layout: dict) -> list:
    """
    Parse one fitz page using the auto-detected layout.
    Returns a list of column-schedule record dicts.
    Returns [] if the page has no usable text.
    """
    words = page.get_text("words")
    if not words:
        return []

    lap_x  = layout["lap_x_centres"]
    lap_n  = layout["lap_names"]
    grp_y  = layout["group_y_centres"]
    grp_n  = layout["group_labels"]
    x_half = layout["lap_x_half"]
    y_half = layout["group_y_half"]
    n_g, n_l = len(grp_n), len(lap_n)

    # Per-cell accumulators
    reinf   = {(g, l): [] for g in range(n_g) for l in range(n_l)}
    sizes   = {}
    dia_acc = {(g, l): [] for g in range(n_g) for l in range(n_l)}
    sp_acc  = {(g, l): [] for g in range(n_g) for l in range(n_l)}
    mix_acc = {}

    for entry in words:
        x0, y0, x1, y1, txt = entry[0], entry[1], entry[2], entry[3], entry[4]
        x = (x0 + x1) / 2
        y = (y0 + y1) / 2

        li = nearest_index(x, lap_x, x_half)
        gi = nearest_index(y, grp_y, y_half)
        if li < 0 or gi < 0:
            continue
        key = (gi, li)

        # ── Reinforcement  "12-16" / "+4-12" / "12-16T" ──────────────────────
        if _REINF_RE.match(txt):
            clean = re.sub(r"^\+", "", txt)
            if not clean.upper().endswith("T"):
                clean += "T"
            if clean not in reinf[key]:
                reinf[key].append(clean)

        # ── Size  "300x950" / "300x500x950" ──────────────────────────────────
        elif _SIZE_RE.match(txt):
            sizes[key] = txt

        # ── Stirrup bar  "8T" / "10T" / "12T" ────────────────────────────────
        elif _DIA_RE.match(txt):
            if txt not in dia_acc[key]:
                dia_acc[key].append(txt)

        # ── Stirrup spacing  multiples of 25 in [50 … 400]  e.g. 100, 150 ──
        elif re.match(r"^\d+$", txt):
            val = int(txt)
            if 50 <= val <= 250 and val % 25 == 0:
                sp = f"{val} C/C"
                if sp not in sp_acc[key]:
                    sp_acc[key].append(sp)

        # ── Concrete mix  "M30" / "M25" ───────────────────────────────────────
        elif _MIX_RE.match(txt):
            mix_acc[key] = txt.upper()

    records = []
    for gi in range(n_g):
        for li in range(n_l):
            key = (gi, li)
            if key not in sizes:
                continue
            records.append({
                "column_no":     grp_n[gi],
                "column_name":   lap_n[li],
                "size":          parse_size(sizes[key]),
                "reinforcement": reinf[key],
                "stirrups": {
                    "dia":     dia_acc[key],
                    "spacing": sorted(sp_acc[key]),
                },
                "mix":           mix_acc.get(key),
                "steel_grade":   None,
            })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Vision-model fallback  (scanned / image-only pages)
# ─────────────────────────────────────────────────────────────────────────────

def _build_vision_prompt(layout: dict) -> str:
    """Build a vision-model prompt dynamically from the detected layout."""
    lap_names  = layout.get("lap_names",    [])
    grp_labels = layout.get("group_labels", [])
    n_l = len(lap_names)
    n_g = len(grp_labels)

    laps_text = "\n".join(f"  Col {i + 1}: {n}" for i, n in enumerate(lap_names))
    rows_text = "\n".join(f"  Row {i + 1:2d}: {g}" for i, g in enumerate(grp_labels))
    ex_col_no   = grp_labels[0] if grp_labels else "AC13, AC14"
    ex_col_name = lap_names[0]  if lap_names  else "5th LAP TO 6th LAP"

    return f"""You are an expert RCC structural drawing extractor.

This drawing is a COLUMN SCHEDULE.

GRID structure:
  Columns (left → right) = {n_l} LAP band repetitions
  Rows    (top → bottom) = {n_g} column groups

Each horizontal band has:
  SECTION A (upper) — cross-section DIAGRAMS with bar labels
  SECTION B (lower) — SUMMARY TABLE with rows: SIZE, RING X, RING Y, MIX

LAP columns (left → right):
{laps_text}

Group rows (top → bottom):
{rows_text}

Total records = {n_g} groups × {n_l} LAP columns = {n_g * n_l}

EXTRACTION RULES
  REINFORCEMENT  (Section A labels)
    "12-16 Tor" → "12-16T"  |  "+4-12 Tor" → "4-12T"
    Valid bar diameter: 8–32;  valid bar count: 2–40

  SIZE  (SIZE row in Section B)
    "300x950"       → width=300, depth=null,  length=950
    "300x500x950"   → width=300, depth=500,   length=950

  STIRRUPS  (RING X / RING Y rows)
    Ignore "N Rings" count text — extract only dia ("8T") and spacing ("100 C/C")

  MIX  (MIX row):  e.g. "M30"

OUTPUT — return ONLY raw JSON, no markdown, no code fences:

{{
  "columns": [
    {{
      "column_no": "{ex_col_no}",
      "column_name": "{ex_col_name}",
      "size": {{"width": 450, "depth": null, "length": 600}},
      "reinforcement": ["12-16T", "4-12T"],
      "stirrups": {{"dia": ["8T"], "spacing": ["100 C/C", "150 C/C"]}},
      "mix": "M30",
      "steel_grade": null
    }}
  ]
}}

Order: group rows top-to-bottom; within each group, LAP columns left-to-right."""


def _parse_model_output(raw: str) -> dict:
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            block = parts[1]
            if block.lower().startswith("json"):
                block = block[4:]
            text = block.strip()
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    return json.loads(text)


def _norm_reinf_entry(r: str):
    r = re.sub(r"^\++", "", str(r).strip())
    r = re.sub(r"\s*[Tt][Oo][Rr]\s*$", "T", r).replace(" ", "")
    m = re.match(r"^(\d+)-(\d+)T?$", r)
    if not m:
        return None
    c, d = int(m.group(1)), int(m.group(2))
    return f"{c}-{d}T" if c <= 40 and d <= 40 else None


def _norm_reinf(lst: list) -> list:
    seen = []
    for r in (lst or []):
        n = _norm_reinf_entry(r)
        if n and n not in seen:
            seen.append(n)
    return seen


def _norm_stirrups(s) -> dict:
    if not isinstance(s, dict):
        return {"dia": [], "spacing": []}
    raw_dia = s.get("dia", []) if isinstance(s.get("dia"), list) else [s.get("dia")]
    dia = []
    for d in raw_dia:
        d = re.sub(r"\s*[Tt][Oo][Rr]\s*", "T", str(d).strip()).replace(" ", "")
        if d and d not in dia:
            dia.append(d)
    raw_sp = s.get("spacing", []) if isinstance(s.get("spacing"), list) else [s.get("spacing")]
    spacing = []
    for sp in raw_sp:
        sp = str(sp).strip()
        if re.search(r"[Rr]ing", sp):
            continue
        sp = re.sub(r"^@\s*", "", sp.upper()).replace(" ", "")
        sp = f"{sp} C/C" if re.match(r"^\d+$", sp) else re.sub(r"C/C$", " C/C", sp).strip()
        if sp and sp not in spacing:
            spacing.append(sp)
    return {"dia": dia, "spacing": spacing}


def _post_process_vision(columns: list) -> list:
    """Normalise and validate vision-model output."""
    out = []
    for col in columns:
        if not isinstance(col, dict):
            continue
        col_no = str(col.get("column_no", "")).strip().upper()
        parts  = [p.strip() for p in re.split(r"[,;]+", col_no)]
        # Accept any column whose parts include an AC-prefixed label
        if not any(re.match(r"^AC\d+$", p) for p in parts):
            continue
        col["column_no"]   = ", ".join(p for p in parts if p)
        col["column_name"] = str(col.get("column_name", "")).strip()
        raw_size = col.get("size")
        if isinstance(raw_size, str):
            col["size"] = parse_size(raw_size)
        elif isinstance(raw_size, dict):
            col["size"] = {
                k: (int(v) if v is not None else None)
                for k, v in raw_size.items()
            }
        else:
            col["size"] = {"width": None, "depth": None, "length": None}
        col["reinforcement"] = _norm_reinf(col.get("reinforcement", []))
        col["stirrups"]      = _norm_stirrups(col.get("stirrups"))
        col["mix"]           = str(col.get("mix", "")).strip().upper() or None
        col["steel_grade"]   = col.get("steel_grade")
        out.append(col)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Merge, sort, report
# ─────────────────────────────────────────────────────────────────────────────

def _merge_pages(records: list) -> list:
    """Deduplicate: keep last occurrence of each (column_no, column_name) pair."""
    seen = {}
    for rec in records:
        key = (rec["column_no"], rec["column_name"])
        seen[key] = rec
    return list(seen.values())


def _sort_records(records: list, layout: dict) -> list:
    grp_order = {g: i for i, g in enumerate(layout["group_labels"])}
    lap_order = {l: i for i, l in enumerate(layout["lap_names"])}
    return sorted(
        records,
        key=lambda r: (
            grp_order.get(r["column_no"],   99),
            lap_order.get(r["column_name"], 99),
        ),
    )


def _report(records: list, layout: dict):
    lap_names  = layout.get("lap_names",    [])
    grp_labels = layout.get("group_labels", [])
    groups = defaultdict(set)
    for r in records:
        groups[r["column_no"]].add(r["column_name"])
    print(f"\n📊 Extraction summary:")
    print(f"   Detected layout : {len(grp_labels)} groups × {len(lap_names)} LAPs"
          f" = {len(grp_labels) * len(lap_names)} expected")
    print(f"   Records found   : {len(records)}")
    for grp in grp_labels:
        missing = set(lap_names) - groups.get(grp, set())
        if missing:
            print(f"   ⚠️  {grp} missing: {sorted(missing)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: str):
    file_name     = os.path.splitext(os.path.basename(pdf_path))[0]
    output_folder = os.path.join(OUTPUT_DIR, file_name)
    os.makedirs(output_folder, exist_ok=True)

    doc = fitz.open(pdf_path)

    # ── 1. Auto-detect layout ────────────────────────────────────────────────
    print(f"\n🔍 Detecting layout from '{file_name}' …")
    layout = detect_layout(doc)

    if not layout:
        # Fully scanned PDF — vision on every page with a generic prompt
        layout = {
            "lap_x_centres":   [], "lap_names":    [],
            "lap_x_half":      200, "group_y_centres": [],
            "group_labels":    [], "group_y_half": 100,
        }

    print(f"  LAP columns ({len(layout['lap_names'])}): {layout['lap_names']}")
    print(f"  Group rows  ({len(layout['group_labels'])}): {layout['group_labels']}")

    # ── 2. Per-page extraction ────────────────────────────────────────────────
    all_records  = []
    vision_prompt = None   # built lazily (only if a scanned page is encountered)
    image_paths   = None   # converted lazily

    for page_no in tqdm(range(len(doc)), desc=f"Processing {file_name}"):
        page    = doc[page_no]
        records = extract_from_pdf_page(page, layout)

        if records:
            print(f"  ✅ Page {page_no + 1}: {len(records)} records via PDF text")
            all_records.extend(records)
        else:
            print(f"  ⚠️  Page {page_no + 1}: no text – falling back to vision model …")
            # Build prompt and convert images only once
            if vision_prompt is None:
                vision_prompt = _build_vision_prompt(layout)
            if image_paths is None:
                image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=300)
            if page_no < len(image_paths):
                raw = extract_from_image(image_paths[page_no], vision_prompt)
                try:
                    parsed = _parse_model_output(raw)
                    vision = _post_process_vision(parsed.get("columns", []))
                    all_records.extend(vision)
                    print(f"     Vision: {len(vision)} records")
                except Exception as e:
                    print(f"     ❌ Vision parse failed: {e}")
                    print(f"        Raw (first 400 chars): {raw[:400]}")

    # ── 3. Merge, sort, save ──────────────────────────────────────────────────
    final = _sort_records(_merge_pages(all_records), layout)
    _report(final, layout)

    output_file = os.path.join(output_folder, f"{file_name}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"columns": final}, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved → {output_file}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print("No PDF files found in INPUT_DIR.")
        return
    for pdf in pdf_files:
        process_pdf(os.path.join(INPUT_DIR, pdf))


if __name__ == "__main__":
    main()


