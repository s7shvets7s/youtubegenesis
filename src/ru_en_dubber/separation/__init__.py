"""Adapters for separating speech from background audio."""

from ru_en_dubber.separation.demucs import (
    DemucsError,
    DemucsExecutionError,
    DemucsNotFoundError,
    DemucsSeparationStage,
)

__all__ = [
    "DemucsError",
    "DemucsExecutionError",
    "DemucsNotFoundError",
    "DemucsSeparationStage",
]

