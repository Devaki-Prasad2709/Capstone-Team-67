from ai.preprocessing.preprocessing_pipeline import PreprocessingPipeline
from ai.computer_vision.inference_engine import InferenceEngine

pipeline = PreprocessingPipeline()

engine = InferenceEngine(
    "ai/computer_vision/models/best.pt"
)

image = "E:/Capstone-Team-67/datasets/processed/drone/isbda/images/10_0.jpg"

result = pipeline.process(image)

if result["status"] == "batch_ready":

    detections = engine.infer(result["batch"])

    print(detections)