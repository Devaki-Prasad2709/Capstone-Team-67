from ai.preprocessing.image_loader import ImageLoader
from ai.preprocessing.image_validator import ImageValidator
from ai.preprocessing.image_resizer import ImageResizer
from ai.preprocessing.deduplicator import Deduplicator
from ai.preprocessing.frame_sampler import FrameSampler
from ai.preprocessing.quality_checker import QualityChecker
from ai.preprocessing.motion_detector import MotionDetector
from ai.preprocessing.batch_manager import BatchManager
from ai.preprocessing.utils import get_timestamp


class PreprocessingPipeline:

    def __init__(self):

        self.loader = ImageLoader()
        self.validator = ImageValidator()
        self.resizer = ImageResizer()

        self.deduplicator = Deduplicator()
        self.sampler = FrameSampler()

        self.quality = QualityChecker()

        self.batch = BatchManager(batch_size=4)
        self.motion = MotionDetector()

    def process(self, image_path):

    # Frame sampling
        # if not self.sampler.should_process():
        #     return {
        #         "status": "skipped",
        #         "reason": "frame_sampling"
        #     }

        # Duplicate detection
        if self.deduplicator.is_duplicate(image_path):
            return {
                "status": "skipped",
                "reason": "duplicate"
            }

        # Load image
        image = self.loader.load(image_path)
        original_shape = image.shape

        # Validate
        if not self.validator.validate(image):
            return {
                "status": "skipped",
                "reason": "invalid_image"
            }

        # Resize
        image = self.resizer.resize(image)

        # Motion detection        
        has_motion, score = self.motion.has_motion(image)

        if not has_motion:
            return {
                "status": "skipped",
                "reason": "no_motion",
                "motion_score": score
            }

        # Deduplication
        if self.deduplicator.is_duplicate(image_path):
            return {
                "status": "skipped",
                "reason": "duplicate"
            }

        # Blur detection
        if self.quality.is_blurry(image):
            return {
                "status": "skipped",
                "reason": "blurry"
            }

        metadata = {
            "image_path": image_path,
            "source": "drone",
            "processed_at": get_timestamp(),
            "original_size": original_shape,
            "processed_size": image.shape
        }

        batch = self.batch.add(image, metadata)

        if batch:
            return {
                "status": "batch_ready",
                "batch": batch
            }

        return {
            "status": "waiting_for_batch"
        }