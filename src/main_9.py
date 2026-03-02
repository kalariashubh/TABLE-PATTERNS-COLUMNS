# import os
# import json
# import re
# from tqdm import tqdm

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


# # -------------------------------
# # CLEANERS
# # -------------------------------

# def clean_mix(mix):
#     if not mix:
#         return None
#     text = str(mix).upper().replace(" ", "")
#     match = re.search(r"M\d+", text)
#     if match:
#         val = match.group()
#         return val.replace("M", "M-")
#     return None


# def clean_reinforcement(reinf_list):
#     if not reinf_list:
#         return []

#     cleaned = []
#     for item in reinf_list:
#         item = item.upper().replace("TOR", "").replace(" ", "")
#         if re.match(r"\d+-\d+T", item):
#             cleaned.append(item)

#     # Remove duplicates
#     return list(dict.fromkeys(cleaned))


# def clean_stirrups(stirrups):
#     if not stirrups:
#         return {"dia": [], "spacing": []}

#     dia_raw = stirrups.get("dia", [])
#     spacing_raw = stirrups.get("spacing", [])

#     # Normalize dia
#     dia = list(dict.fromkeys(
#         [d.upper().replace(" ", "") for d in dia_raw]
#     ))

#     # Normalize spacing
#     spacing = []
#     for s in spacing_raw:
#         s = s.upper().replace("@", "").replace("  ", " ")
#         match = re.search(r"\d+\s*C/C", s)
#         if match:
#             spacing.append(match.group())

#     spacing = list(dict.fromkeys(spacing))

#     return {
#         "dia": dia,
#         "spacing": spacing
#     }



# # -------------------------------
# # MAIN PROCESS
# # -------------------------------

# def process_pdf(pdf_path):

#     file_name = os.path.splitext(os.path.basename(pdf_path))[0]
#     output_folder = os.path.join(OUTPUT_DIR, file_name)
#     os.makedirs(output_folder, exist_ok=True)

#     image_paths = convert_pdf_to_images(
#         pdf_path,
#         output_folder,
#         dpi=700
#     )

#     prompt = load_prompt()
#     final_columns = []

#     for img_path in tqdm(image_paths):

#         result = extract_from_image(img_path, prompt)

#         try:
#             parsed = json.loads(result)
#             columns = parsed.get("columns", [])

#             for col in columns:

#                 # Ensure depth always null
#                 if "size" in col and isinstance(col["size"], dict):
#                     col["size"]["depth"] = None

#                 col["reinforcement"] = clean_reinforcement(
#                     col.get("reinforcement", [])
#                 )

#                 col["stirrups"] = clean_stirrups(
#                     col.get("stirrups")
#                 )

#                 col["mix"] = clean_mix(
#                     col.get("mix")
#                 )

#                 final_columns.append(col)

#         except Exception:
#             continue

#     final_output = {"columns": final_columns}

#     output_file = os.path.join(output_folder, f"{file_name}.json")

#     with open(output_file, "w") as f:
#         json.dump(final_output, f, indent=2)

#     print(f"✅ Output saved to {output_file}")


# def main():

#     os.makedirs(OUTPUT_DIR, exist_ok=True)

#     pdf_files = [
#         f for f in os.listdir(INPUT_DIR)
#         if f.lower().endswith(".pdf")
#     ]

#     for pdf in pdf_files:
#         process_pdf(os.path.join(INPUT_DIR, pdf))


# if __name__ == "__main__":
#     main()



import os
import json
import cv2
from tqdm import tqdm

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


def split_vertical(image, output_folder):
    height, width = image.shape[:2]
    slice_width = width // 4

    paths = []
    for i in range(4):
        left = i * slice_width
        right = (i + 1) * slice_width if i < 3 else width
        crop = image[:, left:right]
        path = os.path.join(output_folder, f"slice_{i+1}.png")
        cv2.imwrite(path, crop)
        paths.append(path)

    return paths


def unique_list(lst):
    return list(dict.fromkeys([x for x in lst if x]))


def convert_size(size_string):
    if not size_string or "x" not in size_string.lower():
        return {"width": None, "depth": None, "length": None}

    try:
        size_string = size_string.lower().replace(" ", "")
        parts = size_string.split("x")

        return {
            "width": int(parts[0]),
            "depth": None,
            "length": int(parts[1])
        }
    except:
        return {"width": None, "depth": None, "length": None}


def clean_reinforcement(reinf_list):
    cleaned = []
    for r in reinf_list:
        r = r.replace(" Tor", "T")
        r = r.replace("Tor", "T")
        r = r.replace(" ", "")
        cleaned.append(r)
    return unique_list(cleaned)


def clean_stirrups(stirrups):
    dia_clean = []
    spacing_clean = []

    for d in stirrups.get("dia", []):
        d = d.replace(" ", "")
        if not d.endswith("T"):
            d = d + "T"
        dia_clean.append(d)

    for s in stirrups.get("spacing", []):
        s = s.replace("c/c", "C/C")
        s = s.replace(" ", "")
        if "C/C" not in s:
            s = s + "C/C"
        spacing_clean.append(s)

    return {
        "dia": unique_list(dia_clean),
        "spacing": unique_list(spacing_clean)
    }


def process_pdf(pdf_path):

    file_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_folder = os.path.join(OUTPUT_DIR, file_name)
    os.makedirs(output_folder, exist_ok=True)

    image_paths = convert_pdf_to_images(pdf_path, output_folder, dpi=950)
    prompt = load_prompt()

    master_levels = []
    column_data = {}

    for img_path in image_paths:

        full_img = cv2.imread(img_path)
        slices = split_vertical(full_img, output_folder)

        for slice_path in tqdm(slices):

            result = extract_from_image(slice_path, prompt)

            try:
                parsed = json.loads(result)
                blocks = parsed.get("columns", [])

                for block in blocks:

                    column_no = block.get("column_no", "").strip().rstrip(",")
                    column_name = block.get("column_name")

                    if not column_no or not column_name:
                        continue

                    # Ignore GROUP labels
                    if column_name.upper().startswith("GROUP"):
                        continue

                    if column_name not in master_levels:
                        master_levels.append(column_name)

                    if column_no not in column_data:
                        column_data[column_no] = {}

                    reinforcement_clean = clean_reinforcement(
                        block.get("reinforcement", [])
                    )

                    stirrups_clean = clean_stirrups(
                        block.get("stirrups", {})
                    )

                    column_data[column_no][column_name] = {
                        "column_no": column_no,
                        "column_name": column_name,
                        "size": convert_size(block.get("size")),
                        "reinforcement": reinforcement_clean,
                        "stirrups": stirrups_clean,
                        "mix": block.get("mix"),
                        "steel_grade": None
                    }

            except Exception as e:
                print("Error parsing slice:", e)
                continue

    # Save master levels file
    levels_output_path = os.path.join(output_folder, "levels.json")
    with open(levels_output_path, "w") as f:
        json.dump({"levels": master_levels}, f, indent=2)

    print("✅ Levels file saved.")

    # Build final output
    final_columns = []

    for column_no, levels in column_data.items():
        for level in master_levels:
            if level in levels:
                final_columns.append(levels[level])
            else:
                final_columns.append({
                    "column_no": column_no,
                    "column_name": level,
                    "size": {"width": None, "depth": None, "length": None},
                    "reinforcement": [],
                    "stirrups": {"dia": [], "spacing": []},
                    "mix": None,
                    "steel_grade": None
                })

    final_output = {"columns": final_columns}

    output_file = os.path.join(output_folder, f"{file_name}.json")

    with open(output_file, "w") as f:
        json.dump(final_output, f, indent=2)

    print(f"✅ Final Output Saved: {output_file}")


def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pdf_files = [
        f for f in os.listdir(INPUT_DIR)
        if f.lower().endswith(".pdf")
    ]

    for pdf in pdf_files:
        process_pdf(os.path.join(INPUT_DIR, pdf))


if __name__ == "__main__":
    main()
