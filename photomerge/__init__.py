"""Merge up to 2000 photos into a single TIFF without reducing resolution."""

from .merger import LAYOUTS, MAX_PHOTOS, MergeError, MergeResult, merge_photos

__all__ = ["LAYOUTS", "MAX_PHOTOS", "MergeError", "MergeResult", "merge_photos"]
__version__ = "2.0.0"
