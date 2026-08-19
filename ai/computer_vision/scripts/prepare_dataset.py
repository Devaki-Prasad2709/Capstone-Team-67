import os
import random
import shutil

random.seed(42)

IMAGE_DIR = "E:/Capstone-Team-67/ai/computer_vision/dataset/images"
LABEL_DIR = "E:/Capstone-Team-67/ai/computer_vision/dataset/labels"

OUTPUT = "ai/computer_vision/dataset"

splits = {
    "train": 0.7,
    "val": 0.2,
    "test": 0.1
}

images = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(".jpg")]
random.shuffle(images)

n = len(images)

train_end = int(n * splits["train"])
val_end = train_end + int(n * splits["val"])

split_data = {
    "train": images[:train_end],
    "val": images[train_end:val_end],
    "test": images[val_end:]
}

for split in split_data:

    os.makedirs(os.path.join(OUTPUT, "images", split), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT, "labels", split), exist_ok=True)

    for img in split_data[split]:

        shutil.copy(
            os.path.join(IMAGE_DIR, img),
            os.path.join(OUTPUT, "images", split, img)
        )

        label = os.path.splitext(img)[0] + ".txt"

        label_path = os.path.join(LABEL_DIR, label)

        if os.path.exists(label_path):
            shutil.copy(
                label_path,
                os.path.join(OUTPUT, "labels", split, label)
            )
        else:
            print(f"Skipping {img} (no label found)")

print("Dataset prepared successfully!")