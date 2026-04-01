import os
import re
import json
from tqdm import tqdm
from collections import defaultdict

from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image


def load_prompt():
    with open(
        os.path.join(os.path.dirname(__file__), "prompt_13.txt"),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()


# ─────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────

def normalize_reinforcement_entry(r):
    r = str(r).strip()
    # Already correct format like "16-T16" — just clean spaces
    r = re.sub(r'\s*-\s*', '-', r)
    r = r.replace(' ', '')
    return r if r else None


def normalize_reinforcement(reinforcement):
    if not reinforcement or not isinstance(reinforcement, list):
        return []
    result = []
    for r in reinforcement:
        # Split on + in case model returns combined string
        for part in str(r).split('+'):
            n = normalize_reinforcement_entry(part.strip())
            if n and n not in result:
                result.append(n)
    return result


def parse_links_string(links_str):
    """
    Parse "LOC 1:-T10 -100 AND LOC 2:-T8 -150"
    Returns (dia_list, spacing_list)
    """
    dia     = []
    spacing = []
    pattern = re.compile(r'LOC\s*\d+\s*:\s*-?\s*[Tt](\d+)\s+-?(\d+)', re.IGNORECASE)
    for m in pattern.finditer(str(links_str)):
        d = f"T{m.group(1)}"
        s = f"{m.group(2)} C/C"
        if d not in dia:
            dia.append(d)
        if s not in spacing:
            spacing.append(s)
    return dia, spacing


def normalize_stirrups(stirrups):
    if not stirrups or not isinstance(stirrups, dict):
        return {"dia": [], "spacing": []}

    raw_dia     = stirrups.get("dia", [])
    raw_spacing = stirrups.get("spacing", [])

    if not isinstance(raw_dia, list):
        raw_dia = [raw_dia]
    if not isinstance(raw_spacing, list):
        raw_spacing = [raw_spacing]

    dia     = []
    spacing = []

    for d in raw_dia:
        d_str = str(d).strip()
        # Could be a full LINKS string passed in dia field
        if 'LOC' in d_str.upper():
            pd, ps = parse_links_string(d_str)
            for x in pd:
                if x not in dia: dia.append(x)
            for x in ps:
                if x not in spacing: spacing.append(x)
        else:
            # Normalize "T10", "10T", "10" → "T10"
            m = re.match(r'^[Tt]?(\d+)[Tt]?$', d_str.replace(' ', ''))
            if m:
                nd = f"T{m.group(1)}"
                if nd not in dia:
                    dia.append(nd)

    for s in raw_spacing:
        s_str = str(s).strip()
        if re.search(r'[Rr]ing', s_str):
            continue
        if 'LOC' in s_str.upper():
            pd, ps = parse_links_string(s_str)
            for x in pd:
                if x not in dia: dia.append(x)
            for x in ps:
                if x not in spacing: spacing.append(x)
        else:
            s_clean = s_str.upper().replace(' ', '')
            if re.match(r'^\d+$', s_clean):
                ns = f"{s_clean} C/C"
            else:
                ns = re.sub(r'C/C$', ' C/C', s_clean).strip()
            if ns and ns not in spacing:
                spacing.append(ns)

    return {"dia": dia, "spacing": spacing}


def normalize_size(size):
    """
    "300 X 1975 (TYPE-33)" → width=300, depth=null, length=1975
    Filter out TYPE numbers (small values ≤ 99) to avoid confusion.
    """
    if isinstance(size, str):
        # Extract all multi-digit numbers ≥ 100 (actual dimensions)
        dims = [int(n) for n in re.findall(r'\b(\d+)\b', size) if int(n) >= 100]
        if len(dims) >= 2:
            return {"width": dims[0], "depth": None, "length": dims[1]}
        elif len(dims) == 1:
            return {"width": dims[0], "depth": None, "length": None}
        return {"width": None, "depth": None, "length": None}

    if isinstance(size, dict):
        def safe_int(v):
            if v is None: return None
            try: return int(v)
            except: return None
        return {
            "width":  safe_int(size.get("width")),
            "depth":  safe_int(size.get("depth")),
            "length": safe_int(size.get("length")),
        }

    return {"width": None, "depth": None, "length": None}


def is_empty_size(size):
    return size.get("width") is None and size.get("length") is None


def normalize_column_no(col_no):
    return str(col_no).strip().upper()


def normalize_column_name(name):
    if not name: return name
    return str(name).strip().upper()


def normalize_grade(val):
    if not val: return None
    return str(val).strip().upper()


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def is_valid_column(col):
    col_no = str(col.get("column_no", "")).strip().upper()
    if not col_no:
        return False
    return bool(re.match(r'^(GC|PC)[A-Z0-9]+', col_no))


def post_process(columns):
    processed = []
    for col in columns:
        if not isinstance(col, dict):
            continue
        if not is_valid_column(col):
            continue

        col["column_no"]     = normalize_column_no(col.get("column_no", ""))
        col["column_name"]   = normalize_column_name(col.get("column_name", ""))
        col["size"]          = normalize_size(col.get("size"))
        col["reinforcement"] = normalize_reinforcement(col.get("reinforcement", []))
        col["stirrups"]      = normalize_stirrups(col.get("stirrups"))
        col["mix"]           = normalize_grade(col.get("mix"))
        col["steel_grade"]   = normalize_grade(col.get("steel_grade"))
        processed.append(col)
    return processed


# ─────────────────────────────────────────────
# Ensure empty cells exist for every combo
# ─────────────────────────────────────────────

def fill_missing_cells(columns):
    """
    After extraction, ensure every (column_no × column_name) combination
    exists. If a cell was skipped by the model, insert it with null values.
    This preserves the complete grid structure.
    """
    all_col_nos  = list(dict.fromkeys(c["column_no"]   for c in columns))
    all_levels   = list(dict.fromkeys(c["column_name"] for c in columns))
    existing     = {(c["column_no"], c["column_name"]): c for c in columns}

    # Detect global mix from any non-null record
    global_mix = next(
        (c["mix"] for c in columns if c.get("mix")), None
    )

    filled = []
    for col_no in all_col_nos:
        for level in all_levels:
            key = (col_no, level)
            if key in existing:
                filled.append(existing[key])
            else:
                # Insert empty cell
                filled.append({
                    "column_no":     col_no,
                    "column_name":   level,
                    "size":          {"width": None, "depth": None, "length": None},
                    "reinforcement": [],
                    "stirrups":      {"dia": [], "spacing": []},
                    "mix":           global_mix,
                    "steel_grade":   None,
                })
    return filled


# ─────────────────────────────────────────────
# Merge pages
# ─────────────────────────────────────────────

def merge_pages(all_columns):
    """
    Deduplicate by (column_no, column_name).
    Prefer non-empty over empty when merging.
    """
    seen = {}
    for col in all_columns:
        key = (col["column_no"], col["column_name"])
        if key not in seen:
            seen[key] = col
        else:
            existing = seen[key]
            # Replace empty with non-empty
            if is_empty_size(existing["size"]) and not is_empty_size(col["size"]):
                seen[key] = col
    return list(seen.values())


# ─────────────────────────────────────────────
# Completeness report
# ─────────────────────────────────────────────

def report_completeness(columns):
    groups     = defaultdict(set)
    all_levels = set()

    for col in columns:
        groups[col["column_no"]].add(col["column_name"])
        all_levels.add(col["column_name"])

    n_groups = len(groups)
    n_levels = len(all_levels)
    actual   = len(columns)
    empty    = sum(1 for c in columns if is_empty_size(c["size"]))
    filled   = actual - empty

    print(f"\n📊 Extraction summary:")
    print(f"   Column groups  : {n_groups}")
    print(f"   Level ranges   : {n_levels}")
    print(f"   Total records  : {actual}  (filled: {filled}, empty: {empty})")
    print(f"   Columns found  : {sorted(groups.keys())}")
    print()


# ─────────────────────────────────────────────
# JSON parsing
# ─────────────────────────────────────────────

def parse_model_output(raw):
    text = raw.strip()
    if '```' in text:
        parts = text.split('```')
        if len(parts) >= 3:
            block = parts[1]
            if block.lower().startswith('json'):
                block = block[4:]
            text = block.strip()
    brace_idx = text.find('{')
    if brace_idx > 0:
        text = text[brace_idx:]
    return json.loads(text)


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────

def process_pdf(pdf_path):
    file_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_folder = os.path.join(OUTPUT_DIR, file_name)
    os.makedirs(output_folder, exist_ok=True)

    image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=300)
    prompt      = load_prompt()
    all_columns = []

    for img_path in tqdm(image_paths, desc=f"Processing {file_name}"):
        result = extract_from_image(img_path, prompt)

        try:
            parsed      = parse_model_output(result)
            raw_columns = parsed.get("columns", [])
            if not isinstance(raw_columns, list):
                continue
            processed = post_process(raw_columns)
            all_columns.extend(processed)

        except Exception as e:
            print(f"  ⚠️  Failed to parse {os.path.basename(img_path)}: {e}")
            debug_path = os.path.splitext(img_path)[0] + "_raw.txt"
            try:
                with open(debug_path, "w", encoding="utf-8") as dbg:
                    dbg.write(result)
                print(f"     Raw output → {debug_path}")
            except Exception:
                pass
            continue

    # Merge duplicate pages
    merged = merge_pages(all_columns)

    # Fill in any missing (column × level) combinations as empty cells
    final_columns = fill_missing_cells(merged)

    report_completeness(final_columns)

    output_file = os.path.join(output_folder, f"{file_name}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"columns": final_columns}, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved → {output_file}  ({len(final_columns)} records)\n")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pdf_files = [
        f for f in os.listdir(INPUT_DIR)
        if f.lower().endswith(".pdf")
    ]

    if not pdf_files:
        print("No PDF files found in INPUT_DIR.")
        return

    for pdf in pdf_files:
        process_pdf(os.path.join(INPUT_DIR, pdf))


if __name__ == "__main__":
    main()