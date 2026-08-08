"""Prepende Protocol: registered, chained prediction commitments."""

from .contract import Contract, Disposition, Resolution
from .anchors import AnchorProvider, ResolutionSigner, build_anchor_statement
from .ledger import (
    IntegrityReport,
    Ledger,
    LedgerIntegrityError,
    RetrofitError,
    STATUS_INCOMPLETE,
    STATUS_OK,
    STATUS_TAMPERED,
    STATUS_UNANCHORED,
)
from .legacy import LegacyContract, LegacyLedger, LegacyResolution, legacy_lock_prediction
from .metrics import reliability, wilson
from .plot import reliability_svg
from .report import MIN_CALIBRATION_N, build_report, grouped_summaries
from .resolvability import resolvability, resolvability_report
from .scoring import (
    brier,
    calibration_table,
    log_score,
    numeric_summary,
    penalized_numeric_summary,
    penalized_probability_summary,
    summary,
)
from .signing import TrustedKey, sign_detached, verify_detached

__version__ = "0.3.0rc1"
__all__ = [
    "Contract", "Disposition", "Resolution", "Ledger", "IntegrityReport",
    "LedgerIntegrityError", "RetrofitError", "STATUS_OK", "STATUS_UNANCHORED",
    "STATUS_INCOMPLETE", "STATUS_TAMPERED", "LegacyContract", "LegacyLedger",
    "LegacyResolution", "legacy_lock_prediction", "brier", "log_score", "summary",
    "calibration_table", "numeric_summary", "penalized_numeric_summary", "penalized_probability_summary",
    "reliability", "wilson", "reliability_svg", "build_report", "grouped_summaries",
    "MIN_CALIBRATION_N", "resolvability", "resolvability_report", "TrustedKey",
    "sign_detached", "verify_detached",
    "AnchorProvider", "ResolutionSigner",
    "build_anchor_statement",
]
