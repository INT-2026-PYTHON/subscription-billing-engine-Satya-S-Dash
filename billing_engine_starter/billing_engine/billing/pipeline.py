"""
build_invoice — PURE function that turns inputs into an Invoice dataclass.

⚠️ NO database calls here. No `datetime.now()`. No PDF. Just math.

The order is FIXED:
    1. base       = strategy.calculate(usage)
    2. discount   = discount.apply(base) if discount else 0
    3. taxable    = base - discount
    4. tax        = tax_calc.apply(taxable)
    5. total      = taxable + tax.total
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from billing_engine.money import Money
from billing_engine.models import (
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind, Subscription, Plan,
)
from billing_engine.pricing.base import PricingStrategy
from billing_engine.discounts.base import Discount, DiscountContext
from billing_engine.taxes.base import TaxCalculator, TaxContext


def build_invoice(
    subscription: Subscription,
    plan: Plan,
    strategy: PricingStrategy,
    discount: Optional[Discount],
    tax_calc: TaxCalculator,
    tax_context: TaxContext,
    usage_quantity: int,
    period_start: date,
    period_end: date,
    invoice_count_so_far: int,
) -> Invoice:

    # 1. base
    base = strategy.calculate(usage_quantity)

    # 2. discount
    if discount:
        discount_context = DiscountContext(invoice_count_so_far)
        discount_amount = discount.apply(base, discount_context)
    else:
        discount_amount = Money("0", base.currency)

    # 3. taxable
    taxable = base - discount_amount

    # 4. tax
    tax_result = tax_calc.apply(taxable, tax_context)

    # 5. total
    total = taxable + tax_result.total

    line_items: list[InvoiceLineItem] = []

    # BASE always
    line_items.append(
        InvoiceLineItem(
            id=None,
            invoice_id=None,
            description="Base charge",
            amount=base,
            kind=LineItemKind.BASE,
        )
    )

    # DISCOUNT only if > 0
    if discount and discount_amount.amount != "0":
        line_items.append(
            InvoiceLineItem(
                id=None,
                invoice_id=None,
                description="Discount",
                amount=discount_amount,
                kind=LineItemKind.DISCOUNT,
            )
        )

    # TAX only if applicable
    # 4. TAX line items (always evaluate tax)
    tax_components = getattr(tax_result, "components", None)

    if tax_components:
    # Multi-component tax (e.g., GST: CGST + SGST)
        for i, t in enumerate(tax_components):
            if t != Money("0", base.currency):
                line_items.append(
                InvoiceLineItem(
                    id=None,
                    invoice_id=None,
                    description=f"Tax {i + 1}",
                    amount=t,
                    kind=LineItemKind.TAX,
                )
            )
    else:
    # Single tax (e.g., VAT)
        if tax_result.total != Money("0", base.currency):
            line_items.append(
            InvoiceLineItem(
                id=None,
                invoice_id=None,
                description="Tax",
                amount=tax_result.total,
                kind=LineItemKind.TAX,
            )
        )

    return Invoice(
        id=None,
        subscription_id=subscription.id,
        period_start=period_start,
        period_end=period_end,
        subtotal=base,
        discount_total=discount_amount,
        tax_total=tax_result.total,
        total=total,
        status=InvoiceStatus.DRAFT,
        issued_at=None,
        pdf_path=None,
        line_items=line_items,
    )