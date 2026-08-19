class BatchManager:

    def __init__(self, batch_size=4):
        self.batch_size = batch_size
        self.images = []
        self.metadata = []

    def add(self, image, metadata):
        self.images.append(image)
        self.metadata.append(metadata)

        if len(self.images) >= self.batch_size:
            batch = {
                "images": self.images.copy(),
                "metadata": self.metadata.copy()
            }

            self.images.clear()
            self.metadata.clear()

            return batch

        return None

    def flush(self):
        """Return remaining images at shutdown."""
        if not self.images:
            return None

        batch = {
            "images": self.images.copy(),
            "metadata": self.metadata.copy()
        }

        self.images.clear()
        self.metadata.clear()

        return batch