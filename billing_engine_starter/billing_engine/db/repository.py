"""
Repositories — the ONLY place SQL lives.

Each repository wraps the Database connection and exposes methods that
take/return domain dataclasses (defined in billing_engine/models/).

⚠️ YOU IMPLEMENT every method body marked TODO.
   The signatures, docstrings, and the LedgerRepository's append-only
   guarantee are already in place — do not change them.

Conventions:
  - Always use parameterized queries (`?` placeholders) — NEVER f-string SQL.
  - Money values are persisted as TEXT using `money.to_storage()`.
  - Dates are persisted as ISO strings (`date.isoformat()`).
"""

from __future__ import annotations
from billing_engine.db import queries as q
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from billing_engine.db.database import Database
from billing_engine.money import Money
from billing_engine.models import (
    Customer,
    Plan, PricingType, BillingPeriod,
    Subscription, SubscriptionStatus,
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind,
    LedgerEntry, LedgerDirection,
)


# ============================================================
# CUSTOMERS
# ============================================================
class CustomerRepository:
    """Persistence boundary for customers."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, customer: Customer) -> Customer:
        with self.db.transaction() as conn:
            new_id = q.insert_customer(
                conn,
                customer.name,
                customer.email,
                customer.country_code,
                customer.state_code,
            )

            row = q.select_customer_by_id(conn, new_id)

            return Customer(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                country_code=row["country_code"],
                state_code=row["state_code"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def get(self, customer_id: int) -> Optional[Customer]:
        with self.db.transaction() as conn:
            row = q.select_customer_by_id(conn, customer_id)

            if row is None:
                return None

            return Customer(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                country_code=row["country_code"],
                state_code=row["state_code"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def find_by_email(self, email: str) -> Optional[Customer]:
        with self.db.transaction() as conn:
            row = q.select_customer_by_email(conn, email)

            if row is None:
                return None

            return Customer(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                country_code=row["country_code"],
                state_code=row["state_code"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def list_all(self) -> list[Customer]:
        with self.db.transaction() as conn:
            rows = q.select_all_customers(conn)

            return [
                Customer(
                    id=row["id"],
                    name=row["name"],
                    email=row["email"],
                    country_code=row["country_code"],
                    state_code=row["state_code"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]
# ============================================================
# PLANS  +  PLAN TIERS
# ============================================================
class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan: Plan) -> Plan:
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO plans (
                name,
                pricing_type,
                billing_period,
                currency,
                config_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                plan.name,
                plan.pricing_type.value,
                plan.billing_period.value,
                plan.currency,
                plan.config_json,
            ),
        )

        new_id = cur.lastrowid

        row = conn.execute(
            """
            SELECT id, name, pricing_type, billing_period, currency, config_json
            FROM plans
            WHERE id = ?
            """,
            (new_id,),
        ).fetchone()

        return Plan(
            id=row[0],
            name=row[1],
            pricing_type=PricingType(row[2]),
            billing_period=BillingPeriod(row[3]),
            currency=row[4],
            config_json=row[5],
        )

    def get(self, plan_id: int) -> Optional[Plan]:
        with self.db.transaction() as conn:
            row = conn.execute(
            """
            SELECT id, name, pricing_type, billing_period, currency, config_json
            FROM plans
            WHERE id = ?
            """,
            (plan_id,),
        ).fetchone()

        if row is None:
            return None

        return Plan(
            id=row[0],
            name=row[1],
            pricing_type=PricingType(row[2]),
            billing_period=BillingPeriod(row[3]),
            currency=row[4],
            config_json=row[5],
        )

    def list_all(self) -> list[Plan]:
        with self.db.transaction() as conn:
            rows = conn.execute(
            """
            SELECT id, name, pricing_type, billing_period, currency, config_json
            FROM plans
            """
            ).fetchall()

        return [
            Plan(
            id=row[0],
            name=row[1],
            pricing_type=PricingType(row[2]),
            billing_period=BillingPeriod(row[3]),
            currency=row[4],
            config_json=row[5],
            )
            for row in rows
        ]


class PlanTierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan_id: int, from_units: int, to_units: Optional[int], unit_price: Money) -> int:
        """Insert a tier; return new id."""
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO plan_tiers (
                plan_id,
                from_units,
                to_units,
                unit_price
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                plan_id,
                from_units,
                to_units,
                unit_price.to_storage(),
            ),
        )

        return cur.lastrowid
    def list_for_plan(self, plan_id: int, currency: str) -> list[tuple[int, Optional[int], Money]]:
        """Return [(from_units, to_units, unit_price)] ordered by from_units.

        Currency is passed in (the plan_tiers table stores only the amount;
        currency lives on the parent plan).
        """
        with self.db.transaction() as conn:
            rows = conn.execute(
            """
            SELECT from_units, to_units, unit_price
            FROM plan_tiers
            WHERE plan_id = ?
            ORDER BY from_units
            """,
            (plan_id,),
            ).fetchall()

        return [
        (
            row[0],
            row[1],
            Money(row[2], currency),
        )
        for row in rows
        ]


# ============================================================
# DISCOUNTS
# ============================================================
class DiscountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, code: str, discount_type: str, value: str, currency: Optional[str] = None) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO discounts (
                code,
                discount_type,
                value,
                currency
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                code,
                discount_type,
                value,
                currency,
            ),
            )

            return cur.lastrowid
    def get_by_code(self, code: str) -> Optional[dict]:
        """Return raw row as dict, or None. (Discount has no dataclass yet — we use a dict for now.)"""
        with self.db.transaction() as conn:
            row = conn.execute(
            """
            SELECT id, code, discount_type, value, currency, valid_until
            FROM discounts
            WHERE code = ?
            """,
            (code,),
            ).fetchone()

        if row is None:
            return None

        return {
        "id": row[0],
        "code": row[1],
        "discount_type": row[2],
        "value": row[3],
        "currency": row[4],
        "valid_until": row[5],
        }


# ============================================================
# SUBSCRIPTIONS
# ============================================================
class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription: Subscription) -> Subscription:
            with self.db.transaction() as conn:
                cur = conn.execute(
                """
                INSERT INTO subscriptions (
                customer_id,
                plan_id,
                status,
                current_period_start,
                current_period_end,
                trial_end,
                discount_id,
                past_due_since
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                subscription.customer_id,
                subscription.plan_id,
                subscription.status.value,
                subscription.current_period_start.isoformat(),
                subscription.current_period_end.isoformat(),
                subscription.trial_end.isoformat() if subscription.trial_end else None,
                subscription.discount_id,
                subscription.past_due_since.isoformat() if subscription.past_due_since else None,
                ),
            )

            new_id = cur.lastrowid

            row = conn.execute(
            """
            SELECT id, customer_id, plan_id, status,
                   current_period_start, current_period_end,
                   trial_end, discount_id, past_due_since
            FROM subscriptions
            WHERE id = ?
            """,
            (new_id,),
            ).fetchone()

            return Subscription(
            id=row[0],
            customer_id=row[1],
            plan_id=row[2],
            status=SubscriptionStatus(row[3]),
            current_period_start=date.fromisoformat(row[4]),
            current_period_end=date.fromisoformat(row[5]),
            trial_end=date.fromisoformat(row[6]) if row[6] else None,
            discount_id=row[7],
            
            past_due_since=date.fromisoformat(row[8]) if row[8] else None,
    )
    def get(self, subscription_id: int) -> Optional[Subscription]:
        with self.db.transaction() as conn:
            row = conn.execute(
            """
            SELECT
                id,
                customer_id,
                plan_id,
                status,
                current_period_start,
                current_period_end,
                trial_end,
                discount_id,
                past_due_since
            FROM subscriptions
            WHERE id = ?
            """,
            (subscription_id,),
            ).fetchone()

        if row is None:
            return None

        return Subscription(
        id=row[0],
        customer_id=row[1],
        plan_id=row[2],
        status=SubscriptionStatus(row[3]),
        current_period_start=date.fromisoformat(row[4]),
        current_period_end=date.fromisoformat(row[5]),
        trial_end=date.fromisoformat(row[6]) if row[6] else None,
        discount_id=row[7],
        past_due_since=date.fromisoformat(row[8]) if row[8] else None,
        )

    def list_all(self) -> list[Subscription]:
        """All subscriptions, regardless of status. Used by BillingCycle trial scan."""
        with self.db.transaction() as conn:
            rows = conn.execute(
            """
            SELECT
                id,
                customer_id,
                plan_id,
                status,
                current_period_start,
                current_period_end,
                trial_end,
                discount_id,
                past_due_since
            FROM subscriptions
            ORDER BY id
            """
            ).fetchall()

        return [
        Subscription(
            id=row[0],
            customer_id=row[1],
            plan_id=row[2],
            status=SubscriptionStatus(row[3]),
            current_period_start=date.fromisoformat(row[4]),
            current_period_end=date.fromisoformat(row[5]),
            trial_end=date.fromisoformat(row[6]) if row[6] else None,
            discount_id=row[7],
            past_due_since=date.fromisoformat(row[8]) if row[8] else None,
        )
        for row in rows
        ]
    def get_due_for_billing(self, as_of: date) -> list[Subscription]:
        """Subscriptions whose current_period_end <= as_of AND status is ACTIVE.
        (Hint: trial subscriptions whose trial_end <= as_of should also become billable —
         either handle that here or transition them to ACTIVE first in BillingCycle.)
        """
        with self.db.transaction() as conn:
            rows = conn.execute(
            """
            SELECT
                id,
                customer_id,
                plan_id,
                status,
                current_period_start,
                current_period_end,
                trial_end,
                discount_id,
                past_due_since
            FROM subscriptions
            WHERE status = ?
              AND current_period_end <= ?
            ORDER BY current_period_end
            """,
            (
                SubscriptionStatus.ACTIVE.value,
                as_of.isoformat(),
            ),
            ).fetchall()

        return [
        Subscription(
            id=row[0],
            customer_id=row[1],
            plan_id=row[2],
            status=SubscriptionStatus(row[3]),
            current_period_start=date.fromisoformat(row[4]),
            current_period_end=date.fromisoformat(row[5]),
            trial_end=date.fromisoformat(row[6]) if row[6] else None,
            discount_id=row[7],
            past_due_since=date.fromisoformat(row[8]) if row[8] else None,
        )
        for row in rows
        ]

    def update_period(self, subscription_id: int, new_start: date, new_end: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE subscriptions
                SET current_period_start = ?, current_period_end = ?
                WHERE id = ?
                """,
                (new_start.isoformat(), new_end.isoformat(), subscription_id),
            )


    def update_status(self, subscription_id: int, status: str, past_due_since: datetime | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE subscriptions
                SET status = ?, past_due_since = ?
                WHERE id = ?
                """,
                (
                    status,
                    past_due_since.isoformat() if past_due_since else None,
                    subscription_id,
                ),
            )

    def update_plan(self, subscription_id: int, new_plan_id: int) -> None:
        """Switch the subscription to a different plan (used by upgrade flow)."""
        # TODO Day 4.
        raise NotImplementedError("Day 4: implement SubscriptionRepository.update_plan")


# ============================================================
# USAGE
# ============================================================
class UsageRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription_id: int, metric: str, quantity: int) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO usage_records (
                subscription_id,
                metric,
                quantity
            )
            VALUES (?, ?, ?)
            """,
            (
                subscription_id,
                metric,
                quantity,
            ),
        )

        return cur.lastrowid


    def sum_for_period(self, subscription_id, metric, start, end):
        cur = self.conn.cursor()
        cur.execute("""
        SELECT SUM(quantity)
        FROM usage_records
        WHERE subscription_id = ?
          AND metric = ?
          AND period_start >= ?
          AND period_end <= ?
        """, (subscription_id, metric, start, end))

        result = cur.fetchone()[0]
        return result or 0
    
# ============================================================
# INVOICES + LINE ITEMS
# ============================================================
class InvoiceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, invoice: Invoice) -> Invoice:
        """Insert invoice (NOT line items — that's the other repo).

        Must respect the UNIQUE(subscription_id, period_start) constraint.
        If a duplicate is attempted, raise sqlite3.IntegrityError naturally
        (caller is responsible for handling it — this gives idempotency).
        """
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO invoices (
                subscription_id,
                period_start,
                period_end,
                currency,
                subtotal,
                discount_total,
                tax_total,
                total,
                status,
                issued_at,
                pdf_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice.subscription_id,
                invoice.period_start.isoformat(),
                invoice.period_end.isoformat(),
                invoice.total.currency,
                invoice.subtotal.to_storage(),
                invoice.discount_total.to_storage(),
                invoice.tax_total.to_storage(),
                invoice.total.to_storage(),
                invoice.status.value,
                invoice.issued_at.isoformat() if invoice.issued_at else None,
                invoice.pdf_path,
            ),
        )

        new_id = cur.lastrowid

        return Invoice(
        id=new_id,
        subscription_id=invoice.subscription_id,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        subtotal=invoice.subtotal,
        discount_total=invoice.discount_total,
        tax_total=invoice.tax_total,
        total=invoice.total,
        status=invoice.status,
        issued_at=invoice.issued_at,
        pdf_path=invoice.pdf_path,
        line_items=invoice.line_items,
        )

    def get(self, invoice_id: int) -> Optional[Invoice]:
        with self.db.transaction() as conn:
            row = conn.execute(
            """
            SELECT
                id,
                subscription_id,
                period_start,
                period_end,
                currency,
                subtotal,
                discount_total,
                tax_total,
                total,
                status,
                issued_at,
                pdf_path
            FROM invoices
            WHERE id = ?
            """,
            (invoice_id,),
            ).fetchone()

            if row is None:
                return None

            return Invoice(
            id=row[0],
            subscription_id=row[1],
            period_start=date.fromisoformat(row[2]),
            period_end=date.fromisoformat(row[3]),
            subtotal=Money(row[5], row[4]),
            discount_total=Money(row[6], row[4]),
            tax_total=Money(row[7], row[4]),
            total=Money(row[8], row[4]),
            status=InvoiceStatus(row[9]),
            issued_at=datetime.fromisoformat(row[10]) if row[10] else None,
            pdf_path=row[11],
            line_items=[],
            )
    def count_for_subscription(self, subscription_id: int) -> int:
        cur = self.db.conn.execute(
            """
            SELECT COUNT(*)
            FROM invoices
            WHERE subscription_id = ?
            """,
            (subscription_id,),
        )
        return int(cur.fetchone()[0])

    def mark_paid(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
            """
            UPDATE invoices
            SET status = ?
            WHERE id = ?
            """,
            (
                InvoiceStatus.PAID.value,
                invoice_id,
            ),
        )

    def mark_failed(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
            """
            UPDATE invoices
            SET status = ?
            WHERE id = ?
            """,
            (
                InvoiceStatus.FAILED.value,
                invoice_id,
            ),
        )
    def set_pdf_path(self, invoice_id: int, path: str) -> None:
        # TODO Day 4.
        raise NotImplementedError("Day 4: implement InvoiceRepository.set_pdf_path")


class InvoiceLineItemRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, line_item: InvoiceLineItem) -> InvoiceLineItem:
        with self.db.transaction() as conn:
            cur = conn.execute(
            """
            INSERT INTO invoice_line_items (
                invoice_id,
                description,
                amount,
                kind
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                line_item.invoice_id,
                line_item.description,
                line_item.amount.to_storage(),
                line_item.kind.value,
            ),
        )

        new_id = cur.lastrowid

        return InvoiceLineItem(
        id=new_id,
        invoice_id=line_item.invoice_id,
        description=line_item.description,
        amount=line_item.amount,
        kind=line_item.kind,
    )
    def list_for_invoice(self, invoice_id: int) -> list[InvoiceLineItem]:
        with self.db.transaction() as conn:

            currency_row = conn.execute(
            """
            SELECT currency
            FROM invoices
            WHERE id = ?
            """,
            (invoice_id,),
            ).fetchone()

        if currency_row is None:
            return []

        currency = currency_row[0]

        rows = conn.execute(
            """
            SELECT
                id,
                invoice_id,
                description,
                amount,
                kind
            FROM invoice_line_items
            WHERE invoice_id = ?
            ORDER BY id
            """,
            (invoice_id,),
        ).fetchall()

        return [
        InvoiceLineItem(
            id=row[0],
            invoice_id=row[1],
            description=row[2],
            amount=Money(row[3], currency),
            kind=LineItemKind(row[4]),
        )
        for row in rows
    ]

# ============================================================
# LEDGER — APPEND-ONLY (do not implement update/delete)
# ============================================================
class LedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self,customer_id: int,invoice_id: int,amount: str,entry_type: str,description: str | None = None,) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO ledger_entries
                (customer_id, invoice_id, amount, entry_type, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    invoice_id,
                    amount,
                    entry_type,
                    description,
                    datetime.utcnow().isoformat(),
                ),
            )
        return int(cur.lastrowid)


    def list_for_customer(self, customer_id: int) -> list[LedgerEntry]:
        cur = self.db.conn.execute(
            """
            SELECT id, customer_id, invoice_id, amount, entry_type, description, created_at
            FROM ledger_entries
            WHERE customer_id = ?
            ORDER BY created_at, id
            """,
            (customer_id,),
        )

        rows = cur.fetchall()

        return [
            LedgerEntry(
                id=row[0],
                customer_id=row[1],
                invoice_id=row[2],
                amount=row[3],
                entry_type=row[4],
                description=row[5],
                created_at=row[6],
            )
            for row in rows
        ]
    
    # ✅ These two methods are intentionally implemented to REJECT — do not override.
    def update(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")


# ============================================================
# PAYMENT ATTEMPTS
# ============================================================
class PaymentAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self,invoice_id: int,attempt_no: int,status: str,failure_reason: Optional[str],next_retry_at: Optional[datetime],) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_attempts (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at.isoformat() if next_retry_at else None,
                    datetime.utcnow().isoformat(),
                ),
            )
            return int(cur.lastrowid)

    def list_for_invoice(self, invoice_id: int) -> list[dict]:
        cur = self.db.conn.execute(
            """
            SELECT
                id,
                invoice_id,
                attempt_no,
                status,
                failure_reason,
                next_retry_at,
                created_at
            FROM payment_attempts
            WHERE invoice_id = ?
            ORDER BY attempt_no ASC
            """,
            (invoice_id,),
        )

        rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "invoice_id": r[1],
                "attempt_no": r[2],
                "status": r[3],
                "failure_reason": r[4],
                "next_retry_at": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def count_for_invoice(self, invoice_id: int) -> int:
        cur = self.db.conn.execute(
            """
            SELECT COUNT(*)
            FROM payment_attempts
            WHERE invoice_id = ?
            """,
            (invoice_id,),
        )
        return int(cur.fetchone()[0])
