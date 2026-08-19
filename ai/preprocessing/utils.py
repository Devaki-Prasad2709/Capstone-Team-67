import os
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


def file_exists(file_path):
    """
    Check if a file exists.
    """
    return os.path.exists(file_path)


def get_timestamp():
    """
    Return current timestamp.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_info(message):
    logger.info(message)


def log_warning(message):
    logger.warning(message)


def log_error(message):
    logger.error(message)