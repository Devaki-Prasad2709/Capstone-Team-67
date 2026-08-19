import os
import cv2

IMAGE_DIR = "E:/Capstone-Team-67/ai/computer_vision/dataset/images/train"
LABEL_DIR = "ai/computer_vision/dataset/labels/train"

# Pick the first training image
image_name = os.listdir(IMAGE_DIR)[0]

image_path = os.path.join(IMAGE_DIR, image_name)
label_path = os.path.join(
    LABEL_DIR,
    os.path.splitext(image_name)[0] + ".txt"
)

image = cv2.imread(image_path)

h, w = image.shape[:2]

with open(label_path) as f:
    lines = f.readlines()

for line in lines:

    cls, xc, yc, bw, bh = map(float, line.split())

    xc *= w
    yc *= h
    bw *= w
    bh *= h

    x1 = int(xc - bw / 2)
    y1 = int(yc - bh / 2)
    x2 = int(xc + bw / 2)
    y2 = int(yc + bh / 2)

    cv2.rectangle(image, (x1, y1), (x2, y2), (0,255,0), 2)
    cv2.putText(
        image,
        f"Class {int(cls)}",
        (x1, y1-5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0,255,0),
        1
    )

cv2.imshow("YOLO Labels", image)
cv2.waitKey(0)
cv2.destroyAllWindows()