"""Ограниченное по размеру множество ключей с истечением срока, безопасное для потоков."""
from collections import OrderedDict
from collections.abc import Hashable
from threading import Lock
import time


class ExpiringKeys:
    """Помнит не более ``capacity`` ключей в течение ``ttl`` секунд; вытесняет давно не читанные."""

    def __init__(self, ttl: float, capacity: int):
        self.ttl = ttl
        self.capacity = capacity
        self.entries: OrderedDict[Hashable, float] = OrderedDict()
        self._lock = Lock()

    def add(self, key: Hashable) -> None:
        with self._lock:
            self.entries[key] = time.monotonic() + self.ttl
            self.entries.move_to_end(key)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)

    def discard(self, key: Hashable) -> None:
        with self._lock:
            self.entries.pop(key, None)

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            until = self.entries.get(key)
            if until is None:
                return False
            if until <= time.monotonic():
                self.entries.pop(key)
                return False
            self.entries.move_to_end(key)
            return True
