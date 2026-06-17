"""
BillingCycle — finds due subscriptions, generates invoices, posts ledger DEBITs,
advances the subscription period. Must be IDEMPOTENT (safe to run twice).
"""
from __future__ import annotations
import sqlite3
from billing_engine.models import SubscriptionStatus

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from billing_engine.db import (
    Database,
    CustomerRepository, PlanRepository, SubscriptionRepository,
    UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository,
    LedgerRepository,
)
from billing_engine.models import Subscription


@dataclass
class BillingResult:
    invoices_created: int
    invoices_skipped_duplicate: int
    trials_activated: int


class BillingCycle:
    """Day-3 deliverable. Day-4 stretch: add `upgrade_subscription(...)`."""

    def __init__(
        self,
        db: Database,
        customer_repo: CustomerRepository,
        plan_repo: PlanRepository,
        subscription_repo: SubscriptionRepository,
        usage_repo: UsageRecordRepository,
        invoice_repo: InvoiceRepository,
        line_item_repo: InvoiceLineItemRepository,
        ledger_repo: LedgerRepository,
        strategy_factory: Callable,    # given a Plan, returns a PricingStrategy
        discount_factory: Callable,    # given a discount_id or None, returns a Discount or None
        tax_factory: Callable,         # given a Customer, returns (TaxCalculator, TaxContext)
    ) -> None:
        self.db = db
        self.customer_repo = customer_repo
        self.plan_repo = plan_repo
        self.subscription_repo = subscription_repo
        self.usage_repo = usage_repo
        self.invoice_repo = invoice_repo
        self.line_item_repo = line_item_repo
        self.ledger_repo = ledger_repo
        self.strategy_factory = strategy_factory
        self.discount_factory = discount_factory
        self.tax_factory = tax_factory

    # --------------------------------------------------------
    def run(self, as_of: date) -> BillingResult:
        """Bill all subscriptions whose current period ends on or before `as_of`."""

        invoices_created = 0
        invoices_skipped_duplicate = 0
        trials_activated = 0

        # -----------------------------
        # Phase 1: Activate expired trials
        # -----------------------------
        for sub in self.subscription_repo.list_all():
            if (
                sub.status == SubscriptionStatus.TRIAL
                and sub.trial_end
                and sub.trial_end <= as_of
            ):
                self.subscription_repo.update_status(
                    sub.id,
                    SubscriptionStatus.ACTIVE,
                    past_due_since=None,
                )
                trials_activated += 1

        # -----------------------------
        # Phase 2: Bill due subscriptions
        # -----------------------------
        due_subscriptions = self.subscription_repo.get_due_for_billing(as_of)

        for sub in due_subscriptions:
            try:
                plan = self.plan_repo.get(sub.plan_id)
                customer = self.customer_repo.get(sub.customer_id)

                strategy = self.strategy_factory(plan)
                discount = self.discount_factory(sub.discount_id)
                tax_calc, tax_context = self.tax_factory(customer)

                usage = self.usage_repo.sum_for_period(
                    sub.id,
                    "units",
                    sub.current_period_start,
                    sub.current_period_end,
                )

                invoice_count = self.invoice_repo.count_for_subscription(sub.id)

                # -----------------------------
                # Build invoice (assumes your builder exists)
                # -----------------------------
                from billing_engine.invoices.builder import build_invoice

                draft = build_invoice(
                    subscription=sub,
                    plan=plan,
                    strategy=strategy,
                    discount=discount,
                    tax_calc=tax_calc,
                    tax_context=tax_context,
                    usage_quantity=usage,
                    period_start=sub.current_period_start,
                    period_end=sub.current_period_end,
                    invoice_count_so_far=invoice_count,
                )

                # -----------------------------
                # SINGLE TRANSACTION (critical for idempotency)
                # -----------------------------
                with self.db.transaction() as conn:
                    # 1. insert invoice
                    cur = conn.execute(
                        """
                        INSERT INTO invoices (
                            subscription_id,
                            customer_id,
                            total_amount,
                            period_start,
                            period_end,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sub.id,
                            sub.customer_id,
                            str(draft.total.amount),
                            sub.current_period_start.isoformat(),
                            sub.current_period_end.isoformat(),
                            date.today().isoformat(),
                        ),
                    )

                    invoice_id = cur.lastrowid

                    # 2. insert line items
                    for item in draft.line_items:
                        conn.execute(
                            """
                            INSERT INTO invoice_line_items (
                                invoice_id,
                                description,
                                quantity,
                                unit_price,
                                total
                            )
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                invoice_id,
                                item.description,
                                item.quantity,
                                str(item.unit_price.amount),
                                str(item.total.amount),
                            ),
                        )

                    # 3. ledger DEBIT
                    conn.execute(
                        """
                        INSERT INTO ledger_entries (
                            customer_id,
                            invoice_id,
                            amount,
                            entry_type,
                            description,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sub.customer_id,
                            invoice_id,
                            str(draft.total.amount),
                            "DEBIT",
                            f"Invoice {invoice_id}",
                            date.today().isoformat(),
                        ),
                    )

                    # 4. advance period
                    self.subscription_repo.update_period(
                        sub.id,
                        draft.next_period_start,
                        draft.next_period_end,
                    )

                invoices_created += 1

            except sqlite3.IntegrityError:
                # duplicate invoice for same period → idempotent skip
                invoices_skipped_duplicate += 1

        return BillingResult(
            invoices_created=invoices_created,
            invoices_skipped_duplicate=invoices_skipped_duplicate,
            trials_activated=trials_activated,
        )

    # --------------------------------------------------------
    def upgrade_subscription(self, subscription_id: int, new_plan_id: int, switch_date: date) -> None:
        """Mid-cycle upgrade — Day 4 stretch."""
        # TODO Day 4
        raise NotImplementedError("Day 4: implement BillingCycle.upgrade_subscription")
