import os
import time
from utils.kafka_config import get_producer

producer = get_producer()

base_folders = [
    "datasets/satellite/spacenet8",
    "datasets/satellite/xbd"
]

all_images = []

# 🔥 RECURSIVE SEARCH (fix)
for base in base_folders:
    for root, dirs, files in os.walk(base):
        for file in files:
            if file.lower().endswith((".tif", ".tiff")):
                full_path = os.path.join(root, file)

                # detect pre/post from path
                if "pre" in root.lower():
                    img_type = "pre"
                elif "post" in root.lower():
                    img_type = "post"
                else:
                    img_type = "unknown"

                all_images.append({
                    "path": full_path,
                    "type": img_type
                })

print(f"Found {len(all_images)} satellite images")

# 🚀 stream
for item in all_images:
    message = {
        "image_id": os.path.basename(item["path"]),
        "image_path": item["path"],
        "image_type": item["type"],
        "timestamp": time.time(),
        "source": "satellite"
    }

    producer.send("satellite-imagery", value=message)
    print("Sent:", message)

    time.sleep(5)