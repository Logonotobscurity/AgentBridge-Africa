"""Hard budget stop — HTTP 402, not a soft warning.

Delegates to ``agentbridge.core.budget_guardian.BudgetGuardian``.
"""

from agentbridge.core.budget_guardian import (  # noqa: F401
    HTTP_402_PAYMENT_REQUIRED,
    BudgetGuardian,
    PaymentRequiredStop,
    guard,
    payment_required_body,
)
