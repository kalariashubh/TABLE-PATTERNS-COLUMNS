import os
import json
from tqdm import tqdm
from PIL import Image

from config import INPUT_DIR, OUTPUT_DIR
from pdf_to_images import convert_pdf_to_images
from vision_extractor import extract_from_image


# ================================
# Load Prompt
# ================================
def load_prompt():
    with open(os.path.join(os.path.dirname(__file__), "prompt_2.txt"), "r") as f:
        return f.read()


# ================================
# Crop Bottom Region (Fallback)
# ================================
def crop_bottom_region(image_path, crop_ratio=0.30):
    """
    Crops bottom portion of image.
    Default = bottom 30%.
    """
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
def normalize_reinforcement(reinf_list):

    cleaned = []

    for item in reinf_list:
        if not item:
            continue

        item = item.upper().strip()

        # Convert TOR → T
        item = item.replace("TOR", "T")

        # Remove spaces
        item = item.replace(" ", "")

        if item not in cleaned:
            cleaned.append(item)

    return cleaned


def has_footings(parsed):
    return isinstance(parsed, dict) and isinstance(parsed.get("footings"), list) and len(parsed["footings"]) > 0


def has_floor_schedule(parsed):
    return isinstance(parsed, dict) and isinstance(parsed.get("floor_schedule"), list) and len(parsed["floor_schedule"]) > 0


# ================================
# Clean Footing
# ================================
def clean_footing(footing):

    # Safe size handling
    size_data = footing.get("size") or {}

    width = size_data.get("width") if isinstance(size_data, dict) else None
    depth = size_data.get("depth") if isinstance(size_data, dict) else None
    length = size_data.get("length") if isinstance(size_data, dict) else None

    # Safe reinforcement handling
    reinf_data = footing.get("reinforcement") or {}
    dia_raw = reinf_data.get("dia", []) if isinstance(reinf_data, dict) else []

    return {
        "footing_id": footing.get("footing_id"),
        "column_id": footing.get("column_id"),
        "size": {
            "width": width,
            "depth": depth,
            "length": length
        },
        "reinforcement": {
            "dia": normalize_reinforcement(dia_raw),
            "spacing": []
        },
        "nos": None,
        "mix": footing.get("mix"),
        "steel_grade": None
    }



# ================================
# Try Extraction (Full → Fallback Crop)
# ================================
def extract_with_fallback(image_path, prompt):

    # ---- First Attempt (Full Image) ----
    result = extract_from_image(image_path, prompt)

    try:
        parsed = json.loads(result)

        if has_footings(parsed) or has_floor_schedule(parsed):
            print("✅ Extracted using full image")
            return parsed

    except:
        pass

    # ---- Fallback: Crop Bottom ----
    print("⚠ Full image extraction failed. Trying cropped bottom region...")

    cropped_path = crop_bottom_region(image_path)

    result = extract_from_image(cropped_path, prompt)

    try:
        parsed = json.loads(result)

        if has_footings(parsed) or has_floor_schedule(parsed):
            print("✅ Extracted using cropped image")
            return parsed

    except:
        pass

    print("❌ Extraction failed even after cropping.")
    return {"footings": []}


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
    all_footings = []
    all_floor_schedule = []

    for img_path in tqdm(image_paths):

        parsed = extract_with_fallback(img_path, prompt)

        if isinstance(parsed.get("footings"), list):
            for f in parsed["footings"]:
                all_footings.append(clean_footing(f))

        if isinstance(parsed.get("floor_schedule"), list):
            all_floor_schedule.extend(parsed["floor_schedule"])

    output_data = {}

    if all_floor_schedule:
        output_data["floor_schedule"] = all_floor_schedule

    if all_footings:
        output_data["footings"] = all_footings

    if not output_data:
        output_data = {"footings": []}

    output_file = os.path.join(file_output_folder, f"{file_name}.json")

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"✅ Output saved to {output_file}")


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