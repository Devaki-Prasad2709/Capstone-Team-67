import os
import time
from utils.kafka_config import get_producer

producer = get_producer()

# ✅ Update path if needed
image_folder = "datasets/processed/drone/isbda/images"

# Get only image files
images = [img for img in os.listdir(image_folder) if img.lower().endswith(".jpg")]

print(f"Found {len(images)} images")

for img in images:
    message = {
        "frame_id": img,
        "image_path": os.path.join(image_folder, img),
        "timestamp": time.time(),
        "source": "drone"
    }

    producer.send("drone-video", value=message)
    print("Sent:", message)

    time.sleep(0.5)