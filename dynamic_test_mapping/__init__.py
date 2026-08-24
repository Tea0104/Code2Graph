"""Dynamic Source-test -> Source-function mapping experiments.

This package is intentionally separate from ``test_mapping``.  It contains
coverage-assisted probes that can fail per project without changing the static
mapping pipeline.
"""

from .models import DynamicFunctionHit, DynamicProbeRecord, DynamicProjectReport

__all__ = [
    "DynamicFunctionHit",
    "DynamicProbeRecord",
    "DynamicProjectReport",
]
