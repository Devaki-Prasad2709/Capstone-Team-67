import os

from ai.computer_vision.vision_pipeline import VisionPipeline

# Create the pipeline
pipeline = VisionPipeline(
    "ai/computer_vision/runs/drone_detector/weights/best.pt"
)

IMAGE_DIR = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images"

for image in sorted(os.listdir(IMAGE_DIR)):

    if not image.lower().endswith(".jpg"):
        continue

    result = pipeline.process(
        os.path.join(IMAGE_DIR, image)
    )

    if result["status"] == "completed":

        print("=" * 60)

        for r in result["results"]:
            print(r["metadata"]["image_path"])
            print(r["detections"])