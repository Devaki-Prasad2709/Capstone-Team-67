import cv2
import os
from ai.preprocessing.utils import file_exists, log_info

class ImageLoader:

    def load(self, image_path):

        if not file_exists(image_path):
            raise FileNotFoundError(f"{image_path} not found")

        log_info(f"Loading {image_path}")

        image = cv2.imread(image_path)

        if image is None:
            raise ValueError("Unable to read image")

        return image

