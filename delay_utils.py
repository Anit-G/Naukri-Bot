import random
import time


def human_delay(min_s, max_s, reason="action"):
    """Sleep for a random uniform delay and log the decision."""
    min_s = max(0.0, float(min_s))
    max_s = max(min_s, float(max_s))
    duration = random.uniform(min_s, max_s)
    print(f"[delay] {reason}: sleeping {duration:.2f}s (range {min_s:.2f}-{max_s:.2f}s)")
    time.sleep(duration)
    return duration


def maybe_cooldown(applied_count, every_n, min_s, max_s):
    """Apply an optional longer cooldown every N successful applications."""
    if every_n and every_n > 0 and applied_count > 0 and applied_count % every_n == 0:
        return human_delay(min_s, max_s, f"cool-down after {applied_count} applications")
    return 0.0
