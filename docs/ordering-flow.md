# Ordering Flow -- API Contract

## POST /api/messages

Accepts a natural-language message. Returns a structured draft order.

### Request
```json
{ "message": "Order 3 cases of Jim Beam for La Zeppa" }
```

### Response
```json
{
  "id": "uuid",
  "message": "Order 3 cases of Jim Beam for La Zeppa",
  "intent": "procurement.order",
  "status": "awaiting_approval",
  "created_at": "2026-03-14T00:00:00.000000",
  "updated_at": "2026-03-14T00:00:00.000000",
  "venue": { "id": "v1", "name": "La Zeppa" },
  "product": {
    "id": "p1",
    "name": "Jim Beam White Label Bourbon 700ml x 12",
    "unit": "case",
    "category": "spirits"
  },
  "supplier": "Bidfood",
  "quantity": 3,
  "line_summary": "3 x Jim Beam White Label Bourbon 700ml x 12 -> La Zeppa via Bidfood"
}
```

## Order Lifecycle

```
POST /api/messages            -> awaiting_approval | needs_clarification
POST /api/orders/{id}/approve -> approved
POST /api/orders/{id}/submit  -> submitted
POST /api/orders/{id}/reject  -> rejected
```

### Status Values
| Status | Meaning |
|---|---|
| `awaiting_approval` | Fully resolved, ready for human review |
| `needs_clarification` | Missing product, venue, or quantity |
| `approved` | Human approved, ready to send |
| `submitted` | Sent to supplier (mocked) |
| `rejected` | Human rejected |

### Intent Values
| Intent | Meaning |
|---|---|
| `procurement.order` | User wants to order stock |
| `unknown` | Could not determine intent |
