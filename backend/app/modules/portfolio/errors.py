"""Portfolio ledger domain errors."""


class LedgerError(Exception):
    """Base error for portfolio ledger operations."""


class LedgerValidationError(LedgerError):
    """Input cannot be represented by the ledger."""


class InsufficientCashError(LedgerError):
    """An operation would consume unavailable or vaulted cash."""


class InsufficientHoldingsError(LedgerError):
    """A sale exceeds the currently held quantity."""


class IdempotencyConflictError(LedgerError):
    """An idempotency key was reused for a different ledger event."""
