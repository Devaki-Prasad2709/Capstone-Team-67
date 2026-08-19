from ultralytics import YOLO

model = YOLO("E:/Capstone-Team-67/ai/computer_vision/models/best.pt")

image_path = "E:/Capstone-Team-67/ai/computer_vision/sample.jpg"

results = model(image_path)

results.show()