import os
import re
import json
from tqdm import tqdm
from collections import defaultdict, OrderedDict
from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image

def load_prompt():
    with open(
        os.path.join(os.path.dirname(__file__), "prompt_10.txt"),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()

def extract_from_pdf_text(pdf_path):
    """
    Parse the column schedule table directly from PDF text.
    This is far more reliable than vision for tabular text PDFs.
    Returns list of column dicts or None if parsing fails.
    """
    import subprocess

    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    raw = result.stdout

    col_match = re.search(
        r'COLUMN\s+NUMBERS\s*\n(.*?)(?:\n\f|\Z)',
        raw, re.DOTALL | re.IGNORECASE
    )
    if not col_match:
        return None

    col_line = col_match.group(1).strip()
    column_ids = re.findall(r'\b([GP][C][A-Z0-9]+)\b', col_line, re.IGNORECASE)
    column_ids = [c.upper() for c in column_ids]
    if not column_ids:
        return None

    n_cols = len(column_ids)

    mix_match = re.search(r'CONCRETE\s+GRADE\s*:\s*(M\d+)', raw, re.IGNORECASE)
    global_mix = mix_match.group(1).upper() if mix_match else None

    lines = raw.split('\n')

    level_pattern = re.compile(
        r'^\s*(BASE|LG|GF|P0[0-9]|P[0-9]+|ECO[-\s]?DECK)\s*$',
        re.IGNORECASE
    )

    col_line_full = ""
    for i, line in enumerate(lines):
        if 'COLUMN' in line.upper() and 'NUMBERS' in line.upper():
            # The actual IDs are in the next line(s)
            for j in range(i+1, min(i+4, len(lines))):
                if re.search(r'[GP][C][A-Z0-9]', lines[j], re.IGNORECASE):
                    col_line_full = lines[j]
                    break
            break

    col_positions = []
    for cid in column_ids:
        pos = col_line_full.find(cid)
        if pos >= 0:
            col_positions.append(pos)
        else:
            col_positions.append(None)

    level_order = [
        "BASE TO LG", "LG TO GF", "GF TO P01",
        "P01 TO P02", "P02 TO P03", "P03 TO P04",
        "P04 TO P05", "P05 TO P06", "P06 TO ECO-DECK"
    ]

    def extract_values_at_positions(line, positions, n):
        """
        Extract n values from `line` based on approximate char positions.
        Returns list of n strings (empty string if no value found at position).
        """
        values = []
        if not positions or None in positions:
            return [""] * n

        for i, pos in enumerate(positions):
            if pos is None:
                values.append("")
                continue
            # Define window: from this column's position to next column's position
            next_pos = positions[i+1] if i+1 < len(positions) else len(line) + 60
            window = line[max(0, pos-5):min(len(line), next_pos)]
            window = window.strip()
            values.append(window)
        return values

    def parse_size(s):
        if not s or not s.strip():
            return {"width": None, "depth": None, "length": None}
        nums = [int(n) for n in re.findall(r'\b(\d+)\b', s) if int(n) >= 100]
        if len(nums) >= 2:
            return {"width": nums[0], "depth": None, "length": nums[1]}
        elif len(nums) == 1:
            return {"width": nums[0], "depth": None, "length": None}
        return {"width": None, "depth": None, "length": None}

    def parse_steel(s):
        if not s or not s.strip():
            return []
        parts = re.split(r'\+', s)
        result = []
        for p in parts:
            p = p.strip()
            m = re.match(r'^(\d+)-[Tt](\d+)$', p.replace(' ', ''))
            if m:
                result.append(f"{m.group(1)}-T{m.group(2)}")
        return result

    def parse_links(s):
        if not s or not s.strip():
            return {"dia": [], "spacing": []}
        dia = []
        spacing = []
        for m in re.finditer(r'LOC\s*\d+\s*:\s*-?\s*[Tt](\d+)\s*[-]?(\d+)', s, re.IGNORECASE):
            d = f"T{m.group(1)}"
            sp = f"{m.group(2)} C/C"
            if d not in dia:
                dia.append(d)
            if sp not in spacing:
                spacing.append(sp)
        return {"dia": dia, "spacing": spacing}

    size_line_re  = re.compile(r'\bSIZE\b', re.IGNORECASE)
    steel_line_re = re.compile(r'\bSTEEL\b', re.IGNORECASE)
    links_line_re = re.compile(r'\bLINKS\b', re.IGNORECASE)
    dim_re        = re.compile(r'\d+\s*[Xx]\s*\d+')

    current_level_from = None
    current_level_to   = None

    size_lines  = []
    steel_lines = []
    links_lines = []
    level_labels = []  # (line_idx, label)

    for i, line in enumerate(lines):
        upper = line.upper()
        if size_line_re.search(line) and dim_re.search(line):
            size_lines.append((i, line))
        elif size_line_re.search(line) and not dim_re.search(line):
            # SIZE row with no values (empty row)
            size_lines.append((i, line))
        if steel_line_re.search(line):
            steel_lines.append((i, line))
        if links_line_re.search(line):
            links_lines.append((i, line))

    triplets = []
    used_steel = set()
    used_links = set()

    for si, sline in size_lines:
        best_steel = None
        for ti, tline in steel_lines:
            if ti > si and ti - si <= 6 and ti not in used_steel:
                best_steel = (ti, tline)
                used_steel.add(ti)
                break
        best_links = None
        ref = best_steel[0] if best_steel else si
        for li, lline in links_lines:
            if li > ref and li - ref <= 6 and li not in used_links:
                best_links = (li, lline)
                used_links.add(li)
                break
        triplets.append((si, sline, best_steel, best_links))

    def get_level_label_near(line_idx, lines):
        """
        Look backward from line_idx to find level labels.
        Returns canonical level name or None.
        """
        label_map = {
            'BASE': 'BASE', 'LG': 'LG', 'GF': 'GF',
            'P01': 'P01', 'P02': 'P02', 'P03': 'P03',
            'P04': 'P04', 'P05': 'P05', 'P06': 'P06',
            'ECO': 'ECO-DECK'
        }
        found = []
        for j in range(max(0, line_idx-8), line_idx+1):
            ln = lines[j].strip().upper()
            for k, v in label_map.items():
                if re.match(rf'^{k}[-\s]?', ln) or ln == k:
                    found.append(v)
        return found

    n_levels = len(level_order)
    n_triplets = len(triplets)

    print(f"   Found {n_triplets} SIZE/STEEL/LINKS triplets for {n_levels} levels")

    assigned = list(zip(reversed(level_order[:n_triplets]), triplets[:n_triplets]))
    levels_in_pdf = {lv for lv, _ in assigned}
    missing_levels = [lv for lv in level_order if lv not in levels_in_pdf]

    def parse_triplet_values(size_line, steel_line_tuple, links_line_tuple, col_positions, n_cols):
        """Extract values for each column from a triplet."""
        sizes  = [""] * n_cols
        steels = [""] * n_cols
        links_ = [""] * n_cols

        def split_by_positions(line, positions):
            vals = []
            for i, pos in enumerate(positions):
                if pos is None:
                    vals.append("")
                    continue
                end = positions[i+1] if i+1 < len(positions) else len(line)+60
                chunk = line[max(0, pos-3): min(len(line), end-1)]
                chunk = re.sub(r'^[GP][C][A-Z0-9]+\s*', '', chunk.strip(), flags=re.IGNORECASE)
                chunk = re.sub(r'^\s*(SIZE|STEEL|LINKS)\s*', '', chunk, flags=re.IGNORECASE)
                vals.append(chunk.strip())
            return vals

        sizes  = split_by_positions(size_line,
                    links_line_tuple[1] if links_line_tuple else size_line, col_positions)
        size_vals  = re.findall(r'\d+\s*[Xx]\s*\d+\s*\([^)]+\)', size_line)
        steel_vals = []
        if steel_line_tuple:
            steel_vals = re.findall(r'\d+-[Tt]\d+(?:\s*\+\s*\d+-[Tt]\d+)*', steel_line_tuple[1])
        links_vals = []
        if links_line_tuple:
            links_vals = re.findall(r'LOC\s*1[^A]+AND\s*LOC\s*2[^\n]+', links_line_tuple[1], re.IGNORECASE)

        return size_vals, steel_vals, links_vals

    columns = []

    for level, (si, sline, steel_t, links_t) in assigned:
        size_vals, steel_vals, links_vals = parse_triplet_values(
            sline, steel_t, links_t, col_positions, n_cols
        )

        n_filled = max(len(size_vals), len(steel_vals), len(links_vals))

        filled_cols = set()

        if size_vals:
            for sv in size_vals:
                idx = sline.find(sv.split('(')[0].strip())
                if idx >= 0 and col_positions:
                    nearest_col = min(range(n_cols),
                        key=lambda i: abs((col_positions[i] or 0) - idx) if col_positions[i] is not None else 9999)
                    filled_cols.add(nearest_col)

        sv_idx = 0
        stv_idx = 0
        lv_idx = 0

        for ci, cid in enumerate(column_ids):
            if ci in filled_cols or (not col_positions[ci] is not None and n_filled > 0 and ci < n_filled):
                sv  = size_vals[sv_idx]   if sv_idx  < len(size_vals)  else ""
                stv = steel_vals[stv_idx] if stv_idx < len(steel_vals) else ""
                lv  = links_vals[lv_idx]  if lv_idx  < len(links_vals) else ""
                sv_idx  += 1
                stv_idx += 1
                lv_idx  += 1
            else:
                sv, stv, lv = "", "", ""

            size_obj = parse_size(sv)
            steel_obj = parse_steel(stv)
            links_obj = parse_links(lv)

            columns.append({
                "column_no":     cid,
                "column_name":   level,
                "size":          size_obj,
                "reinforcement": steel_obj,
                "stirrups":      links_obj,
                "mix":           global_mix,
                "steel_grade":   None,
            })

    for level in missing_levels:
        for cid in column_ids:
            columns.append({
                "column_no":     cid,
                "column_name":   level,
                "size":          {"width": None, "depth": None, "length": None},
                "reinforcement": [],
                "stirrups":      {"dia": [], "spacing": []},
                "mix":           global_mix,
                "steel_grade":   None,
            })

    return columns if columns else None

def normalize_reinforcement_entry(r):
    r = str(r).strip()
    r = re.sub(r'\s*-\s*', '-', r)
    r = r.replace(' ', '')
    return r if r else None


def normalize_reinforcement(reinforcement):
    if not reinforcement or not isinstance(reinforcement, list):
        return []
    result = []
    for r in reinforcement:
        for part in str(r).split('+'):
            n = normalize_reinforcement_entry(part.strip())
            if n and n not in result:
                result.append(n)
    return result


def parse_links_string(links_str):
    dia, spacing = [], []
    pattern = re.compile(r'LOC\s*\d+\s*:\s*-?\s*[Tt](\d+)\s*[-]?(\d+)', re.IGNORECASE)
    for m in pattern.finditer(str(links_str)):
        d = f"T{m.group(1)}"
        s = f"{m.group(2)} C/C"
        if d not in dia: dia.append(d)
        if s not in spacing: spacing.append(s)
    return dia, spacing


def normalize_stirrups(stirrups):
    if not stirrups or not isinstance(stirrups, dict):
        return {"dia": [], "spacing": []}
    raw_dia     = stirrups.get("dia", [])
    raw_spacing = stirrups.get("spacing", [])
    if not isinstance(raw_dia, list):    raw_dia = [raw_dia]
    if not isinstance(raw_spacing, list): raw_spacing = [raw_spacing]
    dia, spacing = [], []
    for d in raw_dia:
        d_str = str(d).strip()
        if 'LOC' in d_str.upper():
            pd, ps = parse_links_string(d_str)
            for x in pd:
                if x not in dia: dia.append(x)
            for x in ps:
                if x not in spacing: spacing.append(x)
        else:
            m = re.match(r'^[Tt]?(\d+)[Tt]?$', d_str.replace(' ', ''))
            if m:
                nd = f"T{m.group(1)}"
                if nd not in dia: dia.append(nd)
    for s in raw_spacing:
        s_str = str(s).strip()
        if re.search(r'[Rr]ing', s_str): continue
        if 'LOC' in s_str.upper():
            pd, ps = parse_links_string(s_str)
            for x in pd:
                if x not in dia: dia.append(x)
            for x in ps:
                if x not in spacing: spacing.append(x)
        else:
            s_clean = s_str.upper().replace(' ', '')
            ns = f"{s_clean} C/C" if re.match(r'^\d+$', s_clean) else re.sub(r'C/C$', ' C/C', s_clean).strip()
            if ns and ns not in spacing: spacing.append(ns)
    return {"dia": dia, "spacing": spacing}


def normalize_size(size):
    if isinstance(size, str):
        dims = [int(n) for n in re.findall(r'\b(\d+)\b', size) if int(n) >= 100]
        if len(dims) >= 2: return {"width": dims[0], "depth": None, "length": dims[1]}
        elif len(dims) == 1: return {"width": dims[0], "depth": None, "length": None}
        return {"width": None, "depth": None, "length": None}
    if isinstance(size, dict):
        def si(v):
            if v is None: return None
            try: return int(v)
            except: return None
        return {"width": si(size.get("width")), "depth": si(size.get("depth")), "length": si(size.get("length"))}
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


def is_valid_column(col):
    col_no = str(col.get("column_no", "")).strip().upper()
    return bool(re.match(r'^[GP][C][A-Z0-9]+', col_no))


def post_process(columns):
    processed = []
    for col in columns:
        if not isinstance(col, dict): continue
        if not is_valid_column(col): continue
        col["column_no"]     = normalize_column_no(col.get("column_no", ""))
        col["column_name"]   = normalize_column_name(col.get("column_name", ""))
        col["size"]          = normalize_size(col.get("size"))
        col["reinforcement"] = normalize_reinforcement(col.get("reinforcement", []))
        col["stirrups"]      = normalize_stirrups(col.get("stirrups"))
        col["mix"]           = normalize_grade(col.get("mix"))
        col["steel_grade"]   = normalize_grade(col.get("steel_grade"))
        processed.append(col)
    return processed


def merge_pages(all_columns):
    seen = {}
    for col in all_columns:
        key = (col["column_no"], col["column_name"])
        if key not in seen:
            seen[key] = col
        else:
            if is_empty_size(seen[key]["size"]) and not is_empty_size(col["size"]):
                seen[key] = col
    return list(seen.values())


def fill_missing_cells(columns):
    all_col_nos = list(dict.fromkeys(c["column_no"]   for c in columns))
    all_levels  = list(dict.fromkeys(c["column_name"] for c in columns))
    existing    = {(c["column_no"], c["column_name"]): c for c in columns}
    global_mix  = next((c["mix"] for c in columns if c.get("mix")), None)
    filled = []
    for col_no in all_col_nos:
        for level in all_levels:
            key = (col_no, level)
            if key in existing:
                filled.append(existing[key])
            else:
                filled.append({
                    "column_no": col_no, "column_name": level,
                    "size": {"width": None, "depth": None, "length": None},
                    "reinforcement": [],
                    "stirrups": {"dia": [], "spacing": []},
                    "mix": global_mix, "steel_grade": None,
                })
    return filled


def report_completeness(columns):
    groups, all_levels = defaultdict(set), set()
    for col in columns:
        groups[col["column_no"]].add(col["column_name"])
        all_levels.add(col["column_name"])
    actual = len(columns)
    empty  = sum(1 for c in columns if is_empty_size(c["size"]))
    filled = actual - empty
    print(f"\n📊 Extraction summary:")
    print(f"   Column groups  : {len(groups)}")
    print(f"   Level ranges   : {len(all_levels)}")
    print(f"   Total records  : {actual}  (filled: {filled}, empty: {empty})")
    print(f"   Columns found  : {sorted(groups.keys())}")
    print()


def parse_model_output(raw):
    text = raw.strip()
    if '```' in text:
        parts = text.split('```')
        if len(parts) >= 3:
            block = parts[1]
            if block.lower().startswith('json'): block = block[4:]
            text = block.strip()
    brace_idx = text.find('{')
    if brace_idx > 0: text = text[brace_idx:]
    return json.loads(text)

def process_pdf(pdf_path):
    file_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_folder = os.path.join(OUTPUT_DIR, file_name)
    os.makedirs(output_folder, exist_ok=True)

    print(f"\n⚙️  Attempting text-based extraction for {file_name}...")
    text_columns = extract_from_pdf_text(pdf_path)

    if text_columns and len(text_columns) > 0:
        print(f"   ✅ Text extraction succeeded ({len(text_columns)} raw records)")
        all_columns = post_process(text_columns)
        method = "text"
    else:
        print(f"   ⚠️  Text extraction failed — falling back to vision model")
        image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=300)
        prompt = load_prompt()
        all_columns = []

        for img_path in tqdm(image_paths, desc=f"Processing {file_name}"):
            result = extract_from_image(img_path, prompt)
            try:
                parsed = parse_model_output(result)
                raw_columns = parsed.get("columns", [])
                if not isinstance(raw_columns, list): continue
                all_columns.extend(post_process(raw_columns))
            except Exception as e:
                print(f"  ⚠️  Failed to parse {os.path.basename(img_path)}: {e}")
                debug_path = os.path.splitext(img_path)[0] + "_raw.txt"
                try:
                    with open(debug_path, "w", encoding="utf-8") as dbg:
                        dbg.write(result)
                except Exception:
                    pass
        method = "vision"

    merged        = merge_pages(all_columns)
    final_columns = fill_missing_cells(merged)
    report_completeness(final_columns)

    output_file = os.path.join(output_folder, f"{file_name}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"columns": final_columns}, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved → {output_file}  ({len(final_columns)} records)  [method: {method}]\n")


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