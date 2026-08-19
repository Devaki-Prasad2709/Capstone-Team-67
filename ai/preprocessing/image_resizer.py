import cv2

class ImageResizer:

    def __init__(self, size=(640, 640)):
        self.size = size

    def resize(self, image):

        return cv2.resize(image, self.size)