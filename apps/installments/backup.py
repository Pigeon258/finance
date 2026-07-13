from .models import InstallmentAdjustment, InstallmentItem, InstallmentPlan

BACKUP_SCHEMA_VERSION = 1
BACKUP_MODELS = (InstallmentPlan, InstallmentItem, InstallmentAdjustment)
