import json
import os

# ==========================
# Update these paths if needed
# ==========================

# import json

# with open("datasets/processed/drone/isbda/annotations/instances.json") as f:
#     data = json.load(f)

# print(data["annotations"][0].keys())
# print(data["annotations"][0])

COCO_JSON = "E:/Capstone-Team-67/ai/computer_vision/datasets/processed/drone/isbda/annotations/instances.json"

IMAGE_DIR = "E:/Capstone-Team-67/ai/computer_vision/datasets/processed/drone/isbda/images"

OUTPUT_LABELS = "E:/Capstone-Team-67/ai/computer_vision/dataset/labels"

os.makedirs(OUTPUT_LABELS, exist_ok=True)

# ==========================

with open(COCO_JSON, "r") as f:
    coco = json.load(f)

# Build image lookup
image_lookup = {}

for image in coco["images"]:
    image_lookup[image["id"]] = {
        "file_name": image["file_name"],
        "width": image["width"],
        "height": image["height"]
    }

print(f"Found {len(image_lookup)} images")

# Create empty label files first
for image in coco["images"]:
    label_name = os.path.splitext(image["file_name"])[0] + ".txt"

    open(os.path.join(OUTPUT_LABELS, label_name), "w").close()

print("Created empty label files")

# Convert annotations
count = 0

for ann in coco["annotations"]:

    image = image_lookup[ann["image_id"]]

    width = image["width"]
    height = image["height"]

    bbox = ann.get("damage_bbox")

    if not bbox or len(bbox) != 4:
        continue

    x, y, w, h = bbox

    if w <= 0 or h <= 0:
        continue

    # COCO -> YOLO
    x_center = (x + w / 2) / width
    y_center = (y + h / 2) / height

    w /= width
    h /= height

    class_id = ann["category_id"] - 1

    label_file = os.path.splitext(image["file_name"])[0] + ".txt"

    with open(os.path.join(OUTPUT_LABELS, label_file), "a") as f:
        f.write(
            f"{class_id} "
            f"{x_center:.6f} "
            f"{y_center:.6f} "
            f"{w:.6f} "
            f"{h:.6f}\n"
        )

    count += 1

print(f"Converted {count} annotations")
print("Done!")

