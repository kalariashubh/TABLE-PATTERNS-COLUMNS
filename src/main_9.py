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
        os.path.join(os.path.dirname(__file__), "prompt_9.txt"),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()


# ─────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────

def normalize_reinforcement_entry(r):
    r = str(r).strip()
    r = r.lstrip("+").strip()
    r = re.sub(r'\s*[Tt][Oo][Rr]\s*$', 'T', r)
    r = re.sub(r'\s*[Tt][Oo][Rr]\b', 'T', r)
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


def normalize_spacing_entry(s):
    s = str(s).strip().upper()
    s = re.sub(r'^@\s*', '', s)
    s = s.replace(' ', '')
    if re.match(r'^\d+$', s):
        return f"{s} C/C"
    s = re.sub(r'C/C$', ' C/C', s).strip()
    return s if s else None


def normalize_stirrups(stirrups):
    if not stirrups or not isinstance(stirrups, dict):
        return {"dia": [], "spacing": []}

    raw_dia = stirrups.get("dia", [])
    raw_spacing = stirrups.get("spacing", [])

    if not isinstance(raw_dia, list):
        raw_dia = [raw_dia]
    if not isinstance(raw_spacing, list):
        raw_spacing = [raw_spacing]

    dia = []
    for d in raw_dia:
        d = str(d).strip()
        d = re.sub(r'\s*[Tt][Oo][Rr]\s*', 'T', d).replace(' ', '')
        if d and d not in dia:
            dia.append(d)

    spacing = []
    for s in raw_spacing:
        s_str = str(s).strip()
        if re.search(r'[Rr]ing', s_str):
            continue
        n = normalize_spacing_entry(s_str)
        if n and n not in spacing:
            spacing.append(n)

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


def normalize_mix(mix):
    if not mix:
        return None
    return str(mix).strip().upper()


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def is_valid_column(col):
    col_no = str(col.get("column_no", "")).strip().upper()
    if not col_no:
        return False
    parts = [p.strip() for p in re.split(r'[,;]+', col_no)]
    return any(p.startswith("AC") for p in parts)


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
        col["mix"]           = normalize_mix(col.get("mix"))
        col["steel_grade"]   = col.get("steel_grade", None)
        processed.append(col)
    return processed


# ─────────────────────────────────────────────
# Merge pages — deduplicate by (column_no, column_name)
# ─────────────────────────────────────────────

def merge_pages(all_columns):
    seen = {}
    for col in all_columns:
        key = (col["column_no"], col["column_name"])
        seen[key] = col
    return list(seen.values())


# ─────────────────────────────────────────────
# Completeness report
# ─────────────────────────────────────────────

def report_completeness(columns):
    groups   = defaultdict(set)
    all_laps = set()

    for col in columns:
        groups[col["column_no"]].add(col["column_name"])
        all_laps.add(col["column_name"])

    n_groups = len(groups)
    n_laps   = len(all_laps)
    expected = n_groups * n_laps
    actual   = len(columns)

    print(f"\n📊 Extraction summary:")
    print(f"   Groups : {n_groups}  |  LAP bands : {n_laps}")
    print(f"   Expected : {expected}  |  Actual : {actual}")

    for col_no, found_laps in sorted(groups.items()):
        missing = all_laps - found_laps
        if missing:
            print(f"   ⚠️  INCOMPLETE: {col_no} — missing: {sorted(missing)}")

    if n_groups < 8:
        print(f"\n   ❗ Only {n_groups} groups found — model may have missed right-side groups.")
    print()


# ─────────────────────────────────────────────
# JSON parsing — handles fenced and raw output
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
    prompt = load_prompt()
    all_columns = []

    for img_path in tqdm(image_paths, desc=f"Processing {file_name}"):
        result = extract_from_image(img_path, prompt)

        try:
            parsed = parse_model_output(result)
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
                print(f"     Raw output saved → {debug_path}")
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