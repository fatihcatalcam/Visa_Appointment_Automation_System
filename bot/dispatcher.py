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
        self.scout_lock = threading.Lock()
        self.location_state = {}
        self.location_events = {}

    def report_date_found(self, raw_results, location=""):
        location = location.lower().strip()
        with self.scout_lock:
            if location not in self.location_state:
                self.location_state[location] = {"is_available": False, "info": [], "last_found": 0}
                self.location_events[location] = threading.Event()
                
            self.location_state[location]["is_available"] = True
            self.location_state[location]["info"] = raw_results
            self.location_state[location]["last_found"] = time.time()
            self.location_events[location].set()
            logger.info(f"🎯 DISPATCHER: Date found! Waking up {location} workers...")

    def report_no_date(self, location=""):
        location = location.lower().strip()
        with self.scout_lock:
            if location not in self.location_state:
                 return
            if time.time() - self.location_state[location]["last_found"] > 300: # 5 minutes expiry
                if self.location_state[location]["is_available"]:
                    logger.info(f"🛑 DISPATCHER: Dates expired for {location}. Workers will sleep.")
                self.location_state[location]["is_available"] = False
                self.location_state[location]["info"] = []
                self.location_events[location].clear()

    def wait_for_dates(self, location="", timeout=None):
        location = location.lower().strip()
        with self.scout_lock:
            if location not in self.location_state:
                self.location_state[location] = {"is_available": False, "info": [], "last_found": 0}
                self.location_events[location] = threading.Event()
            
            if self.location_state[location]["is_available"]:
                return True
                
        return self.location_events[location].wait(timeout=timeout)

# Global singleton instance
scout_dispatcher = ScoutDispatcher()
