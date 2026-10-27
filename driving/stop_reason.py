"""Stoppursachen der IR-/Ultraschall-Fahrmodi."""
from __future__ import annotations

class StopReason(int):

    NONE = int(0)  # Korrektur: frueher Tupel (0,) durch ein ueberzaehliges Komma
    LOST_LINE = int(10)
    OBSTACLE_AHEAD = int(20)
    PROGRAM_STOPPED_BY_USER = int(30)

    STR_REASONS = {
        NONE: "No reason",
        LOST_LINE: "Lost line",
        OBSTACLE_AHEAD: "Obstacle ahead",
        PROGRAM_STOPPED_BY_USER: "Stopped by user"
    }
    
    def __init__(self, reason:int) -> None:
        """_summary_

        Args:
            reason (int): _description_
        """
        self._reason = reason

    def __int__(self) -> int:
        """_summary_

        Returns:
            int: _description_
        """
        return self._reason

    def __str__(self) -> str:
        """_summary_

        Returns:
            str: _description_
        """
        return StopReason.STR_REASONS[self._reason] if self._reason in self.STR_REASONS else "Unknown reason"

    def __eq__(self, value: object) -> bool:
        """_summary_

        Args:
            value (object): _description_

        Returns:
            bool: _description_
        """
        if (isinstance(value, int)):
            return int(value) == self._reason
        return False

