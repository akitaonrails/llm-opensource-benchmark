from decimal import Decimal, localcontext


def convert(amount, from_currency, to_currency, rates):
    with localcontext() as context:
        context.prec = 60
        return Decimal(amount) * rates[from_currency] / rates[to_currency]
