from collections import deque
from PIL import Image
import imagehash


class Deduplicator:

    def __init__(self, window_size=20, threshold=5):

        self.hash_window = deque(maxlen=window_size)

        self.threshold = threshold

    def is_duplicate(self, image_path):

        current_hash = imagehash.phash(Image.open(image_path))

        for old_hash in self.hash_window:

            distance = current_hash - old_hash

            if distance <= self.threshold:
                return True

        self.hash_window.append(current_hash)

        return False