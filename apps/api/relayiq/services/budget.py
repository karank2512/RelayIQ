"""Concurrency-safe campaign budgets.

Reservation pattern: reserve() atomically holds credits before provider calls via a single
guarded UPDATE (spent + reserved + X <= limit evaluated in the database, not in Python),
then commit_spend() converts the hold into actual spend and releases the remainder.
Soft budgets never block but flip into a degradation mode past the warning threshold.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from relayiq.enums import BudgetKind, BudgetPeriod
from relayiq.models import Budget
from relayiq.observability.metrics import BUDGET_BLOCKS


@dataclass
class BudgetState:
    budget: Budget | None
    allowed: bool
    warning: bool = False
    degradation_mode: str | None = None
    reason: str = ""

    @property
    def remaining(self) -> Decimal:
        if self.budget is None:
            return Decimal("Infinity")
        return (
            Decimal(str(self.budget.limit_credits))
            - Decimal(str(self.budget.spent_credits))
            - Decimal(str(self.budget.reserved_credits))
        )


def get_active_budget(session: Session, tenant_id: str, campaign_id: str | None) -> Budget | None:
    q = select(Budget).where(Budget.tenant_id == tenant_id, Budget.is_active.is_(True))
    if campaign_id:
        q = q.where(Budget.campaign_id == campaign_id)
    else:
        q = q.where(Budget.campaign_id.is_(None))
    return session.execute(q.order_by(Budget.created_at)).scalars().first()


def _rollover_if_needed(session: Session, budget: Budget) -> None:
    """Daily budgets reset spent/reserved when the UTC day rolls over."""
    if budget.period != BudgetPeriod.DAILY.value:
        return
    now = datetime.now(UTC)
    start = budget.period_start
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if start is None or now - start >= timedelta(days=1):
        session.execute(
            update(Budget)
            .where(Budget.id == budget.id, Budget.period_start == budget.period_start)
            .values(spent_credits=0, reserved_credits=0, period_start=now.replace(hour=0, minute=0, second=0, microsecond=0))  # noqa: E501
        )
        session.commit()
        session.refresh(budget)


def check(session: Session, budget: Budget | None, amount: float) -> BudgetState:
    """Non-reserving availability check (used by dry-run / decide)."""
    if budget is None:
        return BudgetState(None, allowed=True)
    _rollover_if_needed(session, budget)
    limit = Decimal(str(budget.limit_credits))
    used = Decimal(str(budget.spent_credits)) + Decimal(str(budget.reserved_credits))
    would = used + Decimal(str(amount))
    warning = limit > 0 and (would / limit) >= Decimal(str(budget.warning_threshold))
    if would > limit and budget.kind == BudgetKind.HARD.value:
        return BudgetState(budget, allowed=False, warning=True,
                           degradation_mode=budget.degradation_mode, reason="hard budget exceeded")
    if budget.per_record_max is not None and Decimal(str(amount)) > Decimal(str(budget.per_record_max)):
        return BudgetState(budget, allowed=False, warning=warning,
                           degradation_mode=budget.degradation_mode, reason="per-record maximum exceeded")
    return BudgetState(budget, allowed=True, warning=warning,
                       degradation_mode=budget.degradation_mode if warning else None)


def reserve(session: Session, budget: Budget | None, amount: float) -> BudgetState:
    """Atomically hold `amount` credits. The guard runs inside one UPDATE statement, so
    concurrent reservations can never jointly exceed a hard limit."""
    if budget is None:
        return BudgetState(None, allowed=True)
    _rollover_if_needed(session, budget)
    amt = Decimal(str(round(amount, 4)))
    if budget.per_record_max is not None and amt > Decimal(str(budget.per_record_max)):
        BUDGET_BLOCKS.labels(kind="per_record_max").inc()
        return BudgetState(budget, allowed=False, reason="per-record maximum exceeded",
                           degradation_mode=budget.degradation_mode)

    stmt = (
        update(Budget)
        .where(Budget.id == budget.id)
        .values(reserved_credits=Budget.reserved_credits + amt)
    )
    if budget.kind == BudgetKind.HARD.value:
        stmt = stmt.where(
            Budget.spent_credits + Budget.reserved_credits + amt <= Budget.limit_credits
        )
    result = session.execute(stmt)
    rowcount = int(getattr(result, "rowcount", 0))  # CursorResult at runtime; Result in stubs
    session.commit()
    if rowcount == 0:
        BUDGET_BLOCKS.labels(kind="hard").inc()
        session.refresh(budget)
        return BudgetState(budget, allowed=False, warning=True, reason="hard budget exceeded",
                           degradation_mode=budget.degradation_mode)
    session.refresh(budget)
    limit = Decimal(str(budget.limit_credits))
    used = Decimal(str(budget.spent_credits)) + Decimal(str(budget.reserved_credits))
    warning = limit > 0 and (used / limit) >= Decimal(str(budget.warning_threshold))
    return BudgetState(budget, allowed=True, warning=warning,
                       degradation_mode=budget.degradation_mode if warning else None)


def commit_spend(session: Session, budget: Budget | None, reserved: float, actual: float) -> None:
    """Convert a hold into actual spend; release the unspent remainder."""
    if budget is None:
        return
    res = Decimal(str(round(reserved, 4)))
    act = Decimal(str(round(actual, 4)))
    session.execute(
        update(Budget)
        .where(Budget.id == budget.id)
        .values(
            spent_credits=Budget.spent_credits + act,
            reserved_credits=Budget.reserved_credits - res,
        )
    )
    # Clamp reserved at zero defensively (double release under crash-retry).
    session.execute(
        update(Budget).where(Budget.id == budget.id, Budget.reserved_credits < 0).values(reserved_credits=0)
    )
    session.commit()


def release(session: Session, budget: Budget | None, reserved: float) -> None:
    commit_spend(session, budget, reserved, 0.0)
