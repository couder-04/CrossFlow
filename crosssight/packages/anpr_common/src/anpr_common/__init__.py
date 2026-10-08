"""Shared ANPR contracts: schemas, grammar, fuzzy matching, config, geo."""

from anpr_common.config import Settings, get_settings
from anpr_common.fuzzy import candidates, weighted_edit_distance
from anpr_common.grammar import GrammarResult, normalize_plate
from anpr_common.schemas import Alert, FlowWindow, PlateRead

__all__ = [
    "Alert",
    "FlowWindow",
    "GrammarResult",
    "PlateRead",
    "Settings",
    "candidates",
    "get_settings",
    "normalize_plate",
    "weighted_edit_distance",
]
