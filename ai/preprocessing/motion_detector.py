import cv2
import numpy as np

class MotionDetector:

    def __init__(self, threshold=5000):
        self.previous = None
        self.threshold = threshold

    def has_motion(self, image):

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (320, 320))

        if self.previous is None:
            self.previous = gray
            return True, 100

        diff = cv2.absdiff(self.previous, gray)
        score = np.sum(diff > 25)

        self.previous = gray

        return score > self.threshold, score