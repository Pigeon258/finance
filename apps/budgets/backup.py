from .models import (
    CategoryBudget,
    MonthlyBudget,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
    ReserveMovement,
)

BACKUP_SCHEMA_VERSION = 1
BACKUP_MODELS = (
    MonthlyBudget,
    CategoryBudget,
    ReserveMovement,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
)
