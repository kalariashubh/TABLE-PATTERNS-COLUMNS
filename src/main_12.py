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
        os.path.join(os.path.dirname(__file__), "prompt_12.txt"),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()


# ─────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────

def normalize_reinforcement_entry(r):
    r = str(r).strip().lstrip("+").strip()
    m = re.match(r'^(\d+)\s*[фφΦ@ø]\s*(\d+)\s*[Tt]?$', r)
    if m:
        return f"{m.group(1)}-{m.group(2)}T"
    m = re.match(r'^(\d+)[-\s]+(\d+)\s*[Tt]?$', r.replace(' ', '-'))
    if m:
        return f"{m.group(1)}-{m.group(2)}T"
    r = re.sub(r'\s*[Tt][Oo][Rr]\s*$', 'T', r)
    r = re.sub(r'\s*-\s*', '-', r)
    r = r.replace(' ', '')
    return r if r else None


def normalize_reinforcement(reinforcement):
    if not reinforcement or not isinstance(reinforcement, list):
        return []
    result = []
    for r in reinforcement:
        n = normalize_reinforcement_entry(r)
        if n and n not in result:
            result.append(n)
    return result


def parse_stirrup_string(s):
    s = str(s).strip()
    m = re.match(r'^(\d+)\s*[фφΦ@ø]\s*(\d+)\s*[Cc]/[Cc]', s)
    if m:
        return f"{m.group(1)}T", f"{m.group(2)} C/C"
    return None, None


def normalize_spacing_entry(s):
    s = str(s).strip().upper().replace(' ', '')
    s = re.sub(r'^@\s*', '', s)
    if re.match(r'^\d+$', s):
        return f"{s} C/C"
    s = re.sub(r'C/C$', ' C/C', s).strip()
    return s if s else None


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
        pd, ps = parse_stirrup_string(d_str)
        if pd:
            if pd not in dia:
                dia.append(pd)
            if ps and ps not in spacing:
                spacing.append(ps)
        else:
            d_str = re.sub(r'\s*[Tt][Oo][Rr]\s*', 'T', d_str).replace(' ', '')
            m = re.match(r'^(\d+)[Tt]?$', d_str)
            if m:
                nd = f"{m.group(1)}T"
                if nd not in dia:
                    dia.append(nd)

    for s in raw_spacing:
        s_str = str(s).strip()
        if re.search(r'[Rr]ing', s_str):
            continue
        pd, ps = parse_stirrup_string(s_str)
        if ps:
            if pd and pd not in dia:
                dia.append(pd)
            if ps not in spacing:
                spacing.append(ps)
        else:
            ns = normalize_spacing_entry(s_str)
            if ns and ns not in spacing:
                spacing.append(ns)

    return {"dia": dia, "spacing": spacing}


def normalize_size(size):
    if isinstance(size, str):
        nums = [int(n) for n in re.findall(r'\d+', size)]
        if len(nums) == 2:
            return {"width": nums[0], "depth": None, "length": nums[1]}
        elif len(nums) == 3:
            return {"width": nums[0], "depth": nums[1], "length": nums[2]}
        return {"width": None, "depth": None, "length": None}

    if isinstance(size, dict):
        def safe_int(v):
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        return {
            "width":  safe_int(size.get("width")),
            "depth":  safe_int(size.get("depth")),
            "length": safe_int(size.get("length")),
        }

    return {"width": None, "depth": None, "length": None}


def normalize_column_no(col_no):
    col_no = str(col_no).strip().upper()
    parts = [p.strip() for p in re.split(r'[,;]+', col_no)]
    parts = [p for p in parts if p]
    return ", ".join(parts)


def normalize_column_name(name):
    if not name:
        return name
    return str(name).strip().upper()


def normalize_grade(val):
    if not val:
        return None
    return str(val).strip().upper()


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def is_valid_column(col):
    col_no = str(col.get("column_no", "")).strip().upper()
    if not col_no:
        return False
    parts = [p.strip() for p in re.split(r'[,;]+', col_no)]
    return any(re.match(r'^C\d+$', p) for p in parts)


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
# Merge — last write wins per cell key
# ─────────────────────────────────────────────

def merge_pages(all_columns):
    seen = {}
    for col in all_columns:
        key = (col["column_no"], col["column_name"])
        seen[key] = col
    return list(seen.values())


# ─────────────────────────────────────────────
# Quality check — warn if model copy-pasted
# ─────────────────────────────────────────────

def quality_check(columns):
    groups = defaultdict(list)
    for col in columns:
        if col["column_name"] != "FOOTING":
            groups[col["column_no"]].append(col)

    warned = False
    for col_no, entries in groups.items():
        if len(entries) < 2:
            continue
        reinf_values = [tuple(e["reinforcement"]) for e in entries]
        size_values  = [(e["size"]["width"], e["size"]["depth"]) for e in entries]
        if len(set(reinf_values)) == 1 and len(set(size_values)) == 1:
            print(f"   ⚠️  COPY DETECTED: {col_no} — all floors identical. Model likely hallucinated.")
            warned = True
    if warned:
        print("   → Re-run or manually verify these groups.\n")


# ─────────────────────────────────────────────
# Completeness report
# ─────────────────────────────────────────────

def report_completeness(columns):
    groups     = defaultdict(set)
    all_floors = set()

    for col in columns:
        groups[col["column_no"]].add(col["column_name"])
        all_floors.add(col["column_name"])

    n_groups = len(groups)
    n_floors = len(all_floors)
    actual   = len(columns)

    print(f"\n📊 Extraction summary:")
    print(f"   Column groups : {n_groups}")
    print(f"   Floor bands   : {n_floors}")
    print(f"   Total records : {actual}")
    print(f"   Groups found  : {sorted(groups.keys())}")

    for col_no, found in sorted(groups.items()):
        missing = all_floors - found
        if missing:
            print(f"   ⚠️  INCOMPLETE: {col_no} — missing: {sorted(missing)}")

    if n_groups < 5:
        print(f"\n   ❗ Only {n_groups} groups found — model may have missed groups.")

    quality_check(columns)


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

    final_columns = merge_pages(all_columns)
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