from ultralytics import YOLO

model = YOLO("yolov8n.pt")

model.train(
    data="E:/Capstone-Team-67/ai/computer_vision/dataset/data.yaml",
    epochs=50,
    imgsz=640,
    batch=8,          # Start with 8 (safer on most laptops)
    workers=2,
    project="E:/Capstone-Team-67/ai/computer_vision/runs",
    name="drone_detector",
    exist_ok=True
)