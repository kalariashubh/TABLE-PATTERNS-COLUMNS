import os
import json
from tqdm import tqdm

from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image


def load_prompt():
    with open(
        os.path.join(os.path.dirname(__file__), "prompt_1.txt"),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read()


# -----------------------------------
# NORMALIZATION
# -----------------------------------
def normalize_reinforcement(reinf):

    if not reinf:
        return []

    cleaned = set()

    for r in reinf:
        r = r.upper()
        r = r.replace("Ø", "T")
        r = r.replace("⌀", "T")
        r = r.replace("Φ", "T")
        r = r.replace(" ", "")

        cleaned.add(r)

    return sorted(list(cleaned))


def normalize_steel_grade(steel):

    if not steel:
        return steel

    steel = steel.upper()
    steel = steel.replace("FES00", "FE500")
    steel = steel.replace("FE50O", "FE500")

    return steel.strip()


def clean_column(column):

    column["reinforcement"] = normalize_reinforcement(
        column.get("reinforcement", [])
    )

    column["steel_grade"] = normalize_steel_grade(
        column.get("steel_grade")
    )

    # Pattern-1 → NO STIRRUPS
    column["stirrups"] = {
        "dia": [],
        "spacing": []
    }

    return column


# -----------------------------------
# PROCESS PDF
# -----------------------------------
def process_pdf(pdf_path):

    file_name = os.path.splitext(
        os.path.basename(pdf_path)
    )[0]

    output_folder = os.path.join(
        OUTPUT_DIR,
        file_name
    )

    os.makedirs(output_folder, exist_ok=True)

    print(f"\n📄 Processing {file_name}.pdf")

    image_paths = convert_pdf_to_images(
        pdf_path,
        output_folder,
        dpi=450   # ✅ KEEP 450 (BEST FOR FULL PAGE UNDERSTANDING)
    )

    prompt = load_prompt()
    all_columns = []

    for img in tqdm(image_paths):

        result = extract_from_image(img, prompt)

        try:
            parsed = json.loads(result)

            if "columns" in parsed:
                all_columns.extend(parsed["columns"])

        except Exception:
            print("⚠ JSON parse failed")
            print(result)

    cleaned = [clean_column(c) for c in all_columns]

    # Remove duplicates safely
    unique = []
    seen = set()

    for col in cleaned:
        key = (col["column_no"], col["column_name"])
        if key not in seen:
            seen.add(key)
            unique.append(col)

    final_output = {"columns": unique}

    output_file = os.path.join(
        output_folder,
        f"{file_name}.json"
    )

    with open(output_file, "w") as f:
        json.dump(final_output, f, indent=2)

    print(f"✅ Output saved → {output_file}")


def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pdfs = [
        f for f in os.listdir(INPUT_DIR)
        if f.lower().endswith(".pdf")
    ]

    if not pdfs:
        print("⚠ No PDFs found")
        return

    for pdf in pdfs:
        process_pdf(os.path.join(INPUT_DIR, pdf))


if __name__ == "__main__":
    main()
