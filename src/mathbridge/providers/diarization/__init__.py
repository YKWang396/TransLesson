"""Offline speaker diarization and conservative transcript assignment."""

from .sherpa import apply_diarization, diarize

__all__ = ["apply_diarization", "diarize"]
