import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> tuple[bool, float]:
        now = time.monotonic()
        q = self._hits[key]
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit:
            retry = self.window - (now - q[0])
            return False, max(retry, 0.1)
        q.append(now)
        return True, 0.0

    def peek_blocked(self, key: int) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= self.limit
