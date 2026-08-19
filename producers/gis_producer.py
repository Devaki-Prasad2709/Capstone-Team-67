import os
import time
from utils.kafka_config import get_producer

producer = get_producer()

# ✅ Your folder
folder = "datasets/processed/gis/harvey/imagery"

# Get all jpeg images
images = [img for img in os.listdir(folder) if img.lower().endswith((".jpg", ".jpeg", ".png"))]

print(f"Found {len(images)} GIS images")

for img in images:
    message = {
        "map_id": img,
        "image_path": os.path.join(folder, img),
        "timestamp": time.time(),
        "source": "gis"
    }

    producer.send("gis-data", value=message)
    print("Sent:", message)

    time.sleep(2)  # medium speed