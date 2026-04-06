import os
import json
import re
from tqdm import tqdm
from PIL import Image

from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image


# ================================
# Load Prompt
# ================================
def load_prompt():
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt_12.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


# ================================
# Crop Bottom Region (Fallback)
# ================================
def crop_bottom_region(image_path, crop_ratio=0.30):
    """Crops bottom portion of image. Default = bottom 30%."""
    img = Image.open(image_path)
    width, height = img.size
    crop_height = int(height * crop_ratio)
    cropped = img.crop((0, height - crop_height, width, height))
    cropped_path = image_path.replace(".png", "_cropped.png")
    cropped.save(cropped_path)
    return cropped_path


# ================================
# Reinforcement Normalization
# ================================
def normalize_reinforcement(reinf_raw):
    """
    Normalize reinforcement into clean list.

    Input formats for pattern-12:
      "4 ⌀ 16"    → "4-16T"
      "10 ⌀ 20"   → "10-20T"
      "4-16T"     → already clean
      ["4-16T", "10-20T"] → list input

    Output: ["4-16T", "10-20T"]
    """
    cleaned = []

    if isinstance(reinf_raw, list):
        items = reinf_raw
    elif isinstance(reinf_raw, str):
        items = [reinf_raw]
    else:
        return []

    for item in items:
        if not item:
            continue

        item = str(item).strip()

        # Split on + or newline for multiple groups
        parts = re.split(r'[\+\n]', item)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Replace TOR with T
            part = re.sub(r'(?i)\btor\b', 'T', part)

            # Convert "COUNT ⌀/φ/ø/@ DIA" → "COUNT-DIAT"
            m = re.match(r'^(\d+)\s*[⌀φøΦ@]\s*(\d+)\s*T?$', part, re.IGNORECASE)
            if m:
                count = m.group(1)
                dia   = m.group(2)
                part  = f"{count}-{dia}T"

            # Remove all spaces
            part = re.sub(r'\s+', '', part)

            # Ensure ends with T
            if not part.upper().endswith('T'):
                if re.match(r'^\d+-\d+$', part):
                    part = part + 'T'
                else:
                    continue

            part = part.upper()

            # Final validation: COUNT-DIAT
            if re.match(r'^\d+-\d+T$', part):
                if part not in cleaned:
                    cleaned.append(part)

    return cleaned


# ================================
# Stirrup DIA Normalization
# ================================
def normalize_stirrup_dia(dia_raw):
    """
    Normalize stirrup dia for pattern-12.
    Format: "DIAT"  (just diameter + T, no endcount)

    Input: "16T", "⌀ 16", "16", "16 @ 130 C/C"
    Output: "16T", "12T"
    """
    if not dia_raw:
        return ""

    dia_raw = str(dia_raw).strip()

    # Already correct: "16T", "12T"
    if re.match(r'^\d+T$', dia_raw, re.IGNORECASE):
        return dia_raw.upper()

    # Handle formats like "8-T8" or "8T-8" from other patterns
    m = re.match(r'^(\d+)-?T(\d+)?$', dia_raw, re.IGNORECASE)
    if m:
        return f"{m.group(1)}T"

    # Remove diameter symbols
    dia_raw = re.sub(r'[⌀φøΦ]', '', dia_raw)

    # Replace TOR with T
    dia_raw = re.sub(r'(?i)\btor\b', 'T', dia_raw)

    # Extract first number (the diameter)
    m = re.match(r'\s*(\d+)', dia_raw)
    if m:
        return f"{m.group(1)}T"

    return ""


# ================================
# Stirrup SPACING Normalization
# ================================
def normalize_stirrup_spacing(spacing_raw):
    """
    Normalize stirrup spacing to "NUMBER C/C" format.

    Input: "130 C/C", "130C/C", "130C/S", "130", ["130 C/C"]
    Output: "130 C/C"
    """
    if not spacing_raw:
        return ""

    if isinstance(spacing_raw, list):
        spacing_raw = spacing_raw[0] if spacing_raw else ""

    spacing_raw = str(spacing_raw).strip()

    m = re.search(r'(\d+)', spacing_raw)
    if m:
        val = int(m.group(1))
        if val > 20:
            return f"{val} C/C"

    return ""


# ================================
# Normalize Column Name (floor label)
# ================================
def normalize_column_name(raw):
    """
    Clean up floor label.
    "12 TH FLOOR COLUMN" → "12TH FLOOR COLUMN"
    "BASEMENT COLUMN"    → "BASEMENT COLUMN"
    """
    if not raw:
        return ""
    # Remove extra spaces between number and TH/ST/ND/RD
    cleaned = re.sub(r'(\d+)\s+(TH|ST|ND|RD)\b', r'\1\2', str(raw).strip(), flags=re.IGNORECASE)
    # Collapse multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().upper()
    return cleaned


# ================================
# Normalize Column No (column mark group)
# ================================
def normalize_column_no(raw):
    """
    Normalize column mark group.
    "C1, C18" → "C1,C18"
    "C1,C18"  → "C1,C18"
    "C5"      → "C5"
    """
    if not raw:
        return ""
    # Remove spaces around commas, uppercase
    parts = [p.strip().upper() for p in str(raw).split(',') if p.strip()]
    return ','.join(parts)


# ================================
# Check Parsed Data Validity
# ================================
def has_columns(parsed):
    return (
        isinstance(parsed, dict) and
        isinstance(parsed.get("columns"), list) and
        len(parsed["columns"]) > 0
    )


# ================================
# Try Extraction (Full → Fallback Crop)
# ================================
def extract_with_fallback(image_path, prompt):
    """
    First attempts extraction on full image.
    If that fails, crops the bottom 30% and retries.
    """

    # ---- First Attempt (Full Image) ----
    result = extract_from_image(image_path, prompt)

    print(f"\n[DEBUG] Raw model output (first 400 chars):\n{result[:400]}\n")

    try:
        parsed = json.loads(result)
        if has_columns(parsed):
            print(f"✅ Extracted {len(parsed['columns'])} entries using full image")
            return parsed
    except Exception as e:
        print(f"[DEBUG] JSON parse error: {e}")

    # ---- Fallback: Crop Bottom ----
    print("⚠ Full image extraction failed. Trying cropped bottom region...")

    cropped_path = crop_bottom_region(image_path)
    result = extract_from_image(cropped_path, prompt)

    try:
        parsed = json.loads(result)
        if has_columns(parsed):
            print(f"✅ Extracted {len(parsed['columns'])} entries using cropped image")
            return parsed
    except Exception:
        pass

    print("❌ Extraction failed even after cropping.")
    return {"columns": []}


# ================================
# Clean Column Entry
# ================================
def clean_column(col):
    """
    Normalize a single column entry into the final output format.

    For pattern-12:
      column_no   = COLUMN MARK group (e.g. "C1,C18")
      column_name = FLOOR LABEL       (e.g. "12TH FLOOR COLUMN")
    """

    # ── Size ──────────────────────────────────────────────────────────────────
    size_data = col.get("size") or {}
    if isinstance(size_data, dict):
        width  = size_data.get("width")
        depth  = size_data.get("depth")
        length = size_data.get("length")
    else:
        width = depth = length = None

    # ── Reinforcement ─────────────────────────────────────────────────────────
    reinf_raw     = col.get("reinforcement") or []
    reinforcement = normalize_reinforcement(reinf_raw)

    # ── Stirrups ──────────────────────────────────────────────────────────────
    stirrups_data = col.get("stirrups") or {}
    if isinstance(stirrups_data, dict):
        dia_raw     = stirrups_data.get("dia", "")
        spacing_raw = stirrups_data.get("spacing", "")
    else:
        dia_raw     = str(stirrups_data) if stirrups_data else ""
        spacing_raw = ""

    dia     = normalize_stirrup_dia(dia_raw)
    spacing = normalize_stirrup_spacing(spacing_raw)

    # ── column_no and column_name ──────────────────────────────────────────────
    column_no   = normalize_column_no(col.get("column_no", ""))
    column_name = normalize_column_name(col.get("column_name", ""))

    # ── Mix & Steel ───────────────────────────────────────────────────────────
    mix         = col.get("mix")
    steel_grade = col.get("steel_grade") or "FE500"

    return {
        "column_no":   column_no,
        "column_name": column_name,
        "size": {
            "width":  width,
            "depth":  depth,
            "length": length
        },
        "reinforcement": reinforcement,
        "stirrups": {
            "dia":     dia,
            "spacing": spacing
        },
        "mix":         mix,
        "steel_grade": steel_grade
    }


# ================================
# Process PDF
# ================================
def process_pdf(pdf_path):

    file_name = os.path.splitext(os.path.basename(pdf_path))[0]
    file_output_folder = os.path.join(OUTPUT_DIR, file_name)
    os.makedirs(file_output_folder, exist_ok=True)

    print(f"\n📄 Converting {file_name}.pdf to images...")
    image_paths = convert_pdf_to_images(pdf_path, file_output_folder)

    prompt = load_prompt()

    final_columns = []

    for img_path in tqdm(image_paths, desc=f"Processing {file_name}"):

        parsed = extract_with_fallback(img_path, prompt)

        columns = parsed.get("columns", [])
        if not isinstance(columns, list):
            continue

        for col in columns:
            cleaned = clean_column(col)
            final_columns.append(cleaned)

    # ── Final output ───────────────────────────────────────────────────────────
    output_data = {"columns": final_columns}

    output_file = os.path.join(file_output_folder, f"{file_name}.json")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Output saved to: {output_file}")
    print(f"   Total column entries: {len(final_columns)}")

    if final_columns:
        print("\n-- Preview (first entry) " + "-" * 40)
        print(json.dumps(final_columns[0], indent=2))


# ================================
# Main
# ================================
def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pdf_files = [
        f for f in os.listdir(INPUT_DIR)
        if f.lower().endswith(".pdf")
    ]

    if not pdf_files:
        print("⚠ No PDF files found in input folder.")
        return

    for pdf in pdf_files:
        process_pdf(os.path.join(INPUT_DIR, pdf))


if __name__ == "__main__":
    main()
