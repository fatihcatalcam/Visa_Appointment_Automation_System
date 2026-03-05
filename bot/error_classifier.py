"""
P2: Error Weight Classification System

Provides a minimal, deterministic error classification matrix for:
- HTTP 500, 403, 429
- Timeout / Network failures
- Login / CAPTCHA failures

Each error type defines:
- Proxy impact (fail count + error type for threshold logic)
- Account risk impact (points added to risk engine)
- Retry behavior (max retries + backoff sequence)
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ErrorWeight:
    """Immutable error classification with proxy/account/retry weights."""
    proxy_fails: int          # Number of proxy fail increments
    proxy_error_type: str     # Error type string for proxy_manager.report_failure()
    account_risk_points: int  # Points added to Redis risk engine
    max_retries: int          # Max retry attempts before giving up
    backoff_sequence: Tuple[int, ...]  # Backoff delays in seconds (indexed by attempt)


# ════════════════════════════════════════════════════════════════════════════
# Classification Matrix
# ════════════════════════════════════════════════════════════════════════════

ERROR_WEIGHTS = {
    "500":     ErrorWeight(1, "general", 5,  3, (30, 60, 120)),
    "403":     ErrorWeight(1, "403",     15, 2, (60, 300)),
    "429":     ErrorWeight(1, "429",     0,  5, (120, 240, 480, 960, 1800)),
    "timeout": ErrorWeight(1, "timeout", 0,  3, (30, 60, 120)),
    "network": ErrorWeight(2, "general", 0,  2, (10, 30)),
    "login":   ErrorWeight(0, "",        15, 3, (60, 60, 60)),
    "captcha": ErrorWeight(0, "",        5,  2, (30, 30)),
}


def classify_error(exception: Exception = None, http_status: Optional[int] = None) -> str:
    """
    Classify an error into one of the known categories.
    Priority: HTTP status > exception string > default.

    Returns one of: '500', '403', '429', 'timeout', 'network', 'login', 'captcha'
    """
    # 1. HTTP status takes priority
    if http_status == 403:
        return "403"
    if http_status == 429:
        return "429"
    if http_status and http_status >= 500:
        return "500"

    # 2. Exception string matching
    if exception:
        err_str = str(exception).lower()
        if "timeout" in err_str or "timed out" in err_str:
            return "timeout"
        if any(k in err_str for k in ("connection", "network", "refused", "reset")):
            return "network"
        if any(k in err_str for k in ("login", "giriş", "giri̇ş")):
            return "login"
        if "captcha" in err_str:
            return "captcha"

    # 3. Default: treat unknown as server error
    return "500"


def get_backoff(error_type: str, attempt: int) -> int:
    """
    Get the backoff delay for a given error type and attempt number.
    Clamps to the last value in the sequence if attempt exceeds length.
    """
    weight = ERROR_WEIGHTS.get(error_type, ERROR_WEIGHTS["500"])
    seq = weight.backoff_sequence
    return seq[min(attempt, len(seq) - 1)]


def get_weight(error_type: str) -> ErrorWeight:
    """Get the full ErrorWeight for a given error type."""
    return ERROR_WEIGHTS.get(error_type, ERROR_WEIGHTS["500"])
