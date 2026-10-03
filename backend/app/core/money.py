"""Money (COP) in DECIMAL(12,2): one representation everywhere (HU-004).

Amounts have at most 2 decimal places and are never rounded: a value with
more decimals is rejected, not rounded. Python Decimal, never float.
"""

from decimal import Decimal

MONEY_PLACES = 2
MONEY_MAX = Decimal("9999999999.99")  # DECIMAL(12,2)
CENT = Decimal("0.01")


def format_cop(amount: Decimal) -> str:
    """'$ 150.000,00' (es-CO, the same shape as Intl.NumberFormat('es-CO', COP))."""
    sign = "-" if amount < 0 else ""
    units, cents = f"{abs(amount):.2f}".split(".")
    return f"{sign}$ {int(units):,}".replace(",", ".") + f",{cents}"
