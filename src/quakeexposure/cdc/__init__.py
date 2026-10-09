"""CDC transacional da carteira: stage durável, apply atômico e offsets após o commit."""

from quakeexposure.cdc.store import CdcError, apply_pending, stage

__all__ = ["CdcError", "apply_pending", "stage"]
