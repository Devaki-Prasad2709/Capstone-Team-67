import numpy as np

class ImageValidator:

    def validate(self, image):

        if image is None:
            return False

        if not isinstance(image, np.ndarray):
            return False

        h, w = image.shape[:2]

        if h < 100 or w < 100:
            return False

        return True