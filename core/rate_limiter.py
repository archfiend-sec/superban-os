import asyncio
import random
import time
import logging
from typing import Optional

logger = logging.getLogger("RateLimiter")

class CircuitBreakerOpen(Exception):
    """Raised when Telegram imposes an excessive flood penalty."""
    pass

class AdaptiveRateLimiter:
    """Intelligent rate limiter with dynamic backoff, jitter, and safety circuit breaking."""
    
    def __init__(self, initial_delay: float = 0.8, min_delay: float = 0.15, max_delay: float = 10.0):
        self.current_delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.flood_count = 0
        self.success_count = 0
        self.is_circuit_broken = False
        self.circuit_resume_time = 0.0

    async def wait(self):
        """Wait with proportional jitter to avoid resonant API hammering."""
        now = time.time()
        if self.is_circuit_broken:
            if now < self.circuit_resume_time:
                wait_left = self.circuit_resume_time - now
                logger.warning(f"🛑 Circuit breaker open! Sleeping {wait_left:.1f}s...")
                await asyncio.sleep(wait_left)
            self.is_circuit_broken = False

        if self.current_delay > 0:
            jitter = random.uniform(0.85, 1.25)
            await asyncio.sleep(self.current_delay * jitter)

    def on_flood(self, seconds: int):
        """React to FloodWaitError."""
        self.flood_count += 1
        self.current_delay = min(max(self.current_delay * 1.7, 1.2), self.max_delay)
        
        # If flood wait is severe (> 120s), open circuit breaker
        if seconds > 120:
            self.is_circuit_broken = True
            self.circuit_resume_time = time.time() + seconds
            logger.critical(f"🚨 Excessive FloodWait ({seconds}s). Tripping circuit breaker.")
        else:
            logger.warning(f"⚠️ FloodWait ({seconds}s). Delay adjusted to {self.current_delay:.2f}s")

    def on_success(self):
        """Reward consistent successful calls with speed boosts."""
        self.success_count += 1
        if self.success_count % 25 == 0:
            self.current_delay = max(self.current_delay * 0.93, self.min_delay)
            logger.debug(f"⚡ Acceleration boost: Delay reduced to {self.current_delay:.2f}s")

    def get_status(self):
        return {
            "current_delay": round(self.current_delay, 2),
            "min_delay": round(self.min_delay, 2),
            "max_delay": round(self.max_delay, 2),
            "flood_count": self.flood_count,
            "success_count": self.success_count,
            "circuit_open": self.is_circuit_broken
        }
