"""Compatibility alias: one authoritative storage implementation."""
import sys
import storage
sys.modules[__name__] = storage
