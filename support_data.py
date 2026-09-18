"""Synthetic order fixtures. Policy retrieval is implemented in rag.py."""
from rag import retrieve

ORDERS = {
    "RF-1004": {"id": "RF-1004", "owner": "demo", "amount": 15900, "days": 3, "used": True, "status": "delivered"},
    "RF-1001": {"id": "RF-1001", "owner": "demo", "amount": 29900, "days": 3, "used": False, "status": "delivered"},
    "RF-1002": {"id": "RF-1002", "owner": "demo", "amount": 12900, "days": 12, "used": True, "status": "delivered"},
    "RF-1003": {"id": "RF-1003", "owner": "demo", "amount": 8900, "days": 0, "used": False, "status": "shipping"},
}

