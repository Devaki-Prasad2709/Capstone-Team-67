from ultralytics import YOLO

model = YOLO("E:/Capstone-Team-67/ai/computer_vision/models/best.pt")

metrics = model.val()

print(metrics)