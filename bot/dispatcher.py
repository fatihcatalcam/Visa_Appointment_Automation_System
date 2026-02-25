import threading
import time
import logging

logger = logging.getLogger(__name__)

class ScoutDispatcher:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ScoutDispatcher, cls).__new__(cls)
                cls._instance._init_dispatcher()
            return cls._instance

    def _init_dispatcher(self):
        self.is_date_available = False
        self.available_dates_info = [] # e.g. [{"category": "Tourism", "day": "12"}]
        self.last_found_time = 0
        self.scout_lock = threading.Lock()
        
        # When date is found, we set this event to wake up sleeping workers
        self.wake_up_event = threading.Event()

    def report_date_found(self, raw_results):
        """Scout calls this when it finds dates"""
        with self.scout_lock:
            self.is_date_available = True
            self.available_dates_info = raw_results
            self.last_found_time = time.time()
            self.wake_up_event.set() # Wake up all waiting threads
            logger.info("🎯 DISPATCHER: Date found! Waking up all workers...")

    def report_no_date(self):
        """Scout calls this when no dates are found"""
        with self.scout_lock:
            # If a date was found recently, keep it alive for a few minutes so workers have time to book
            if time.time() - self.last_found_time > 300: # 5 minutes expiry
                if self.is_date_available:
                    logger.info("🛑 DISPATCHER: Dates expired. Workers will sleep.")
                self.is_date_available = False
                self.available_dates_info = []
                self.wake_up_event.clear()

    def wait_for_dates(self, timeout=None):
        """Workers call this to sleep until dates are available"""
        if self.is_date_available:
            return True
        return self.wake_up_event.wait(timeout=timeout)

# Global singleton instance
scout_dispatcher = ScoutDispatcher()
