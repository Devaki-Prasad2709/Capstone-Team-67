from ultralytics import YOLO

from ai.preprocessing.preprocessing_pipeline import PreprocessingPipeline


class VisionPipeline:

    def __init__(self, model_path="yolo26s.pt"):

        self.pipeline = PreprocessingPipeline()

        self.model = YOLO(model_path)

    def process(self, image_path):

        result = self.pipeline.process(image_path)

        if result["status"] != "batch_ready":
            return result

        images = result["batch"]["images"]
        metadata = result["batch"]["metadata"]

        predictions = self.model(images)

        outputs = []

        for prediction, meta in zip(predictions, metadata):

            detections = []

            if prediction.boxes is not None:

                for box in prediction.boxes:

                    detections.append({
                        "class_id": int(box.cls.item()),
                        "confidence": float(box.conf.item()),
                        "bbox": box.xyxy[0].tolist()
                    })

            outputs.append({
                "metadata": meta,
                "detections": detections
            })

        return {
            "status": "completed",
            "results": outputs
        }
