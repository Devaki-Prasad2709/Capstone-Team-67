import time

class FrameSampler:

    def __init__(self, interval=2):

        self.interval = interval
        self.last_time = 0

    def should_process(self):

        current = time.time()

        if current - self.last_time >= self.interval:

            self.last_time = current
            return True

        return False