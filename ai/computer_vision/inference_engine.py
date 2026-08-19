from ultralytics import YOLO


class InferenceEngine:

    def __init__(self, model_path):

        self.model = YOLO(model_path)

    def infer(self, batch):

        images = batch["images"]
        metadata = batch["metadata"]

        results = self.model(images)

        output = []

        for result, meta in zip(results, metadata):

            detections = []

            if result.boxes is not None:

                for box in result.boxes:

                    detections.append({

                        "class_id": int(box.cls),

                        "confidence": float(box.conf),

                        "bbox": box.xyxy.cpu().numpy().tolist()[0]

                    })

            output.append({

                "metadata": meta,

                "detections": detections

            })

        return output