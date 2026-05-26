"""Simple async rate limiter for IBKR pacing-sensitive endpoints."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Awaitable, Callable, Deque, TypeVar

T = TypeVar("T")


class ApiCircuitBreaker:
    def __init__(self, max_calls: int = 45, window_seconds: float = 1.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: Deque[float] = deque()
        self._lock = asyncio.Lock()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > self.window_seconds:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                sleep_for = self.window_seconds - (now - self._calls[0])
                await asyncio.sleep(max(0.0, sleep_for))
            self._calls.append(time.monotonic())
        return await fn()
