import cv2

class QualityChecker:

    def is_blurry(self, image, threshold=100):

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        variance = cv2.Laplacian(
            gray,
            cv2.CV_64F
        ).var()

        return variance < threshold