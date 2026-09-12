from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RecycleManager:
    """Manages safe recycling of PyTgCalls instances when idle."""

    def __init__(self) -> None:
        self._recycle_pending: dict[int, bool] = {}

    def should_recycle(self, helper_id: int, active_calls: int) -> bool:
        """Determine if an instance should be recycled.

        Only idle instances (zero active calls) that have been marked
        for recycle are eligible.
        """
        if active_calls > 0:
            return False
        return self._recycle_pending.get(helper_id, False)

    def mark_for_recycle(self, helper_id: int) -> None:
        self._recycle_pending[helper_id] = True

    def clear_recycle(self, helper_id: int) -> None:
        self._recycle_pending.pop(helper_id, None)

    async def try_recycle(
        self,
        helper_id: int,
        active_calls: int,
        recreate_fn=None,
    ) -> bool:
        """Attempt to recycle a PyTgCalls instance if safe.

        Returns True if recycled, False if skipped (active calls).
        """
        if not self.should_recycle(helper_id, active_calls):
            if active_calls > 0:
                logger.debug(
                    "Skipping recycle for helper %s: %d active calls",
                    helper_id, active_calls,
                )
            return False

        logger.info("Recycling PyTgCalls instance for helper %s", helper_id)
        self.clear_recycle(helper_id)

        if recreate_fn is not None:
            try:
                await recreate_fn(helper_id)
            except Exception:
                logger.exception("Failed to recreate instance for helper %s", helper_id)
                return False

        return True

    def get_pending(self) -> dict[int, bool]:
        return dict(self._recycle_pending)


recycle_manager = RecycleManager()
