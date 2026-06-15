"""prepende — pre-registered, hash-locked predictions with calibration scoring.

The one novel idea, made enforceable: a predictor (any AI or human) commits a
falsifiable prediction BEFORE the outcome is known, with its scoring rule AND
evaluation regime locked into a content hash. You cannot move the goalposts after
the fact — any edit breaks the hash, and resolving under a changed regime is refused.
Over many predictions the ledger yields a real calibration curve: is the predictor's
stated confidence trustworthy?
"""
from .contract import Contract, Resolution, lock_prediction
from .ledger import Ledger, RetrofitError
from .scoring import brier, log_score, summary, calibration_table, numeric_summary
from .metrics import reliability, wilson
from .resolvability import resolvability, resolvability_report
from .plot import reliability_svg
from .report import build_report

__version__ = "0.2.0"
__all__ = [
    "Contract", "Resolution", "lock_prediction",
    "Ledger", "RetrofitError",
    "brier", "log_score", "summary", "calibration_table", "numeric_summary",
    "reliability", "wilson", "reliability_svg", "build_report",
    "resolvability", "resolvability_report",
]
