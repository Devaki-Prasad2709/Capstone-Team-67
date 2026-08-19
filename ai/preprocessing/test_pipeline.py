# from preprocessing_pipeline import PreprocessingPipeline

# pipeline = PreprocessingPipeline()

# # Replace with any drone image
# image_path = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images/9_0.jpg"

# result = pipeline.process(image_path)

# if result is None:
#     print("Image was skipped.")
# else:
#     print("Image successfully processed!")
#     print(result.shape)

# from preprocessing_pipeline import PreprocessingPipeline

# pipeline = PreprocessingPipeline()

# image_path = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images/10_0.jpg"

# result = pipeline.process(image_path)

# print(result)

# if result["status"] == "processed":
#     print("Image processed successfully!")
#     print(result["image"].shape)

# else:
#     print(f"Skipped because: {result['reason']}")

# import os

# from preprocessing_pipeline import PreprocessingPipeline

# pipeline = PreprocessingPipeline()

# IMAGE_DIR = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images"

# stats = {
#     "processed": 0,
#     "duplicate": 0,
#     "frame_sampling": 0,
#     "invalid_image": 0,
#     "blurry": 0
# }

# for img in sorted(os.listdir(IMAGE_DIR)):

#     if not img.lower().endswith((".jpg", ".jpeg", ".png")):
#         continue

#     result = pipeline.process(os.path.join(IMAGE_DIR, img))

#     if result["status"] == "processed":
#         stats["processed"] += 1

#     else:
#         stats[result["reason"]] += 1

# print("\n===== PREPROCESSING REPORT =====")

# total = sum(stats.values())

# for k, v in stats.items():
#     print(f"{k:<18}: {v}")

# print("-" * 35)
# print(f"Total Images      : {total}")
# print(f"Reduction         : {(1 - stats['processed']/total)*100:.2f}%")

import os

from preprocessing_pipeline import PreprocessingPipeline

pipeline = PreprocessingPipeline()

IMAGE_DIR = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images"

for img in sorted(os.listdir(IMAGE_DIR)):

    if not img.endswith(".jpg"):
        continue

    result = pipeline.process(os.path.join(IMAGE_DIR, img))

    if result["status"] == "batch_ready":

        print("=================================")
        print("Batch Ready")
        print("Batch Size:", len(result["batch"]["images"]))
        print("=================================")