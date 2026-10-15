"""AGG-1: when to run an aggregation cycle (design doc, "trigger contract"):

- a fallback timer (5 min) is always armed, so the feed never goes stale;
- each source.poll.completed (re)starts a ~20 s debounce window, so a burst of
  connectors finishing together coalesces into one run, capped at ~90 s
  max-wait from the first pending notice, so a steady trickle can't starve it;
- pending notices collapse into one "run owed" flag, never a queue of runs;
- a spike (trend or cluster score with velocityLabel "spike") skips the
  debounce and runs as soon as possible.

Pure state machine on caller-supplied timestamps (seconds), so it's tested
against scripted event sequences.
"""
from typing import Optional

TIMER, POLL, SPIKE = "timer", "poll", "spike"


class TriggerState:
    def __init__(self, fallback_s: float = 300.0, debounce_s: float = 20.0, max_wait_s: float = 90.0,
                 started_at: float = 0.0):
        self.fallback_s, self.debounce_s, self.max_wait_s = fallback_s, debounce_s, max_wait_s
        self.last_run_at = started_at
        self.pending_since: Optional[float] = None
        self.debounce_until: Optional[float] = None
        self.spike_pending = False

    def on_poll_completed(self, now: float) -> None:
        if self.pending_since is None:
            self.pending_since = now
        self.debounce_until = now + self.debounce_s  # each notice restarts the debounce

    def on_spike(self, now: float) -> None:
        self.spike_pending = True

    def due(self, now: float) -> Optional[str]:
        """Why a run is owed now, or None."""
        if self.spike_pending:
            return SPIKE
        if self.pending_since is not None and (now >= self.debounce_until or
                                               now >= self.pending_since + self.max_wait_s):
            return POLL
        if now >= self.last_run_at + self.fallback_s:
            return TIMER
        return None

    def seconds_until_due(self, now: float) -> float:
        if self.spike_pending:
            return 0.0
        deadlines = [self.last_run_at + self.fallback_s]
        if self.pending_since is not None:
            deadlines.append(min(self.debounce_until, self.pending_since + self.max_wait_s))
        return max(0.0, min(deadlines) - now)

    def mark_ran(self, now: float) -> None:
        """A run started: everything owed so far is covered by it."""
        self.last_run_at = now
        self.pending_since = self.debounce_until = None
        self.spike_pending = False
