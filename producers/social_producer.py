import json
import time
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.kafka_config import get_producer

producer = get_producer()

file_path = "datasets/processed/social/crisismmd/posts.jsonl"

with open(file_path, "r") as f:
    for line in f:
        data = json.loads(line)
        # print(data)

        message = {
            "id": data.get("id"),
            "text": data.get("text") or "",
            "hazard": data.get("hazard"),
            "timestamp": time.time(),
            "source": "twitter"
        }

        producer.send("social-posts", value=message)
        print("Sent:", message)

        time.sleep(1)