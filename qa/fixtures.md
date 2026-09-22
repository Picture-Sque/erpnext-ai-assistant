# Fixtures

## Customers
- Full name 1: Pinnacle Media Works
- Full name 2: 
- Ambiguous name (matches 2+): Acme Corp

## Suppliers
- Supplier 1: Zuckerman Security Ltd.
- Supplier 2: MA Inc.

## Items
- ITEM-DESK-001 stock: 580
- ITEM-DESK-001 reorder level: Not Set
- ITEM-CHAIR-002 stock: 130
- ITEM-CHAIR-002 reorder level: Not Set
- [LOW ITEM] name: SKU009
- [LOW ITEM] stock (<50): 36
- [HIGH ITEM] name: ITEM-DESK-001
- [HIGH ITEM] stock (>5): 580
- ITEM-DESK-001 has any PO?: True (expected none, but found one)
- Low-stock threshold default: Not Set

## Time/Other
- Month with zero sales: January 2026
- Real Sales Order ID: SAL-ORD-2026-00039
- Real Purchase Order ID: PUR-ORD-2026-00002
