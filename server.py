import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from supabase import create_client, Client
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prex-mcp")

supabase_url = os.environ.get("SUPABASE_URL", "")
supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
supabase: Client = create_client(supabase_url, supabase_key)

mcp = FastMCP(
    "prex-customers-mcp",
)


async def health(request):
    return JSONResponse({"status": "ok"})


@mcp.tool()
def list_customers(
    country: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List customers with optional filters"""
    q = supabase.table("customers").select("*", count="exact")
    if country:
        q = q.eq("country", country)
    if status:
        q = q.eq("status", status)
    if search:
        q = q.or_(
            f"first_name.ilike.%{search}%"
            f",last_name.ilike.%{search}%"
            f",email.ilike.%{search}%"
        )
    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    data = q.execute()
    return json.dumps({"customers": data.data, "total": data.count}, indent=2, default=str)


@mcp.tool()
def get_customer(id: Optional[str] = None, email: Optional[str] = None) -> str:
    """Get full details of a customer by ID or email"""
    q = supabase.table("customers").select("*")
    if id:
        q = q.eq("id", id)
    elif email:
        q = q.eq("email", email)
    else:
        return json.dumps({"error": "Provide either id or email"})
    data = q.execute()
    if data.data:
        return json.dumps(data.data[0], indent=2, default=str)
    return json.dumps({"error": "Customer not found"})


@mcp.tool()
def get_customer_accounts(customer_id: str) -> str:
    """Get all accounts for a customer"""
    data = (
        supabase.table("accounts")
        .select("*")
        .eq("customer_id", customer_id)
        .order("opened_at", desc=True)
        .execute()
    )
    return json.dumps(data.data, indent=2, default=str)


@mcp.tool()
def get_customer_cards(customer_id: str) -> str:
    """Get all cards for a customer"""
    data = (
        supabase.table("cards")
        .select("*")
        .eq("customer_id", customer_id)
        .order("issued_at", desc=True)
        .execute()
    )
    return json.dumps(data.data, indent=2, default=str)


@mcp.tool()
def get_customer_transactions(
    customer_id: str,
    days_back: int = 90,
    limit: int = 50,
) -> str:
    """Get recent transactions for a customer"""
    since = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
    data = (
        supabase.table("transactions")
        .select("*, accounts!inner(customer_id)")
        .eq("accounts.customer_id", customer_id)
        .gte("transactions.created_at", since)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return json.dumps(data.data, indent=2, default=str)


@mcp.tool()
def get_customer_loans(customer_id: str) -> str:
    """Get loan information for a customer"""
    data = (
        supabase.table("loans")
        .select("*")
        .eq("customer_id", customer_id)
        .order("created_at", desc=True)
        .execute()
    )
    return json.dumps(data.data, indent=2, default=str)


@mcp.tool()
def get_customer_full_profile(
    customer_id: Optional[str] = None,
    email: Optional[str] = None,
) -> str:
    """Get complete customer profile with accounts, cards, transactions, and loans"""
    q = supabase.table("customers").select("*")
    if customer_id:
        q = q.eq("id", customer_id)
    elif email:
        q = q.eq("email", email)
    else:
        return json.dumps({"error": "Provide customer_id or email"})
    customer_data = q.execute()
    if not customer_data.data:
        return json.dumps({"error": "Customer not found"})
    customer = customer_data.data[0]
    accounts = supabase.table("accounts").select("*").eq("customer_id", customer["id"]).execute()
    cards = supabase.table("cards").select("*").eq("customer_id", customer["id"]).execute()
    transactions = (
        supabase.table("transactions")
        .select("*, accounts!inner(customer_id)")
        .eq("accounts.customer_id", customer["id"])
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    loans = supabase.table("loans").select("*").eq("customer_id", customer["id"]).execute()
    return json.dumps(
        {
            "customer": customer,
            "accounts": accounts.data,
            "cards": cards.data,
            "recent_transactions": transactions.data,
            "loans": loans.data,
        },
        indent=2, default=str,
    )


@mcp.tool()
def get_dashboard_summary() -> str:
    """Get aggregate KPIs: total customers, total balance, transaction volume, active loans"""
    cust = supabase.table("customers").select("*", count="exact").execute()
    acc = supabase.table("accounts").select("balance, currency").eq("status", "active").execute()
    tx = (
        supabase.table("transactions")
        .select("amount, status")
        .gte("created_at", (datetime.utcnow() - timedelta(days=30)).isoformat())
        .execute()
    )
    loans = supabase.table("loans").select("amount, status").eq("status", "active").execute()
    active_accounts = acc.data if acc.data else []
    tx_data = tx.data if tx.data else []
    loan_data = loans.data if loans.data else []
    return json.dumps({
        "total_customers": cust.count or 0,
        "active_accounts": len(active_accounts),
        "total_balance_ars": sum(float(a["balance"]) for a in active_accounts if a.get("currency") == "ARS"),
        "total_balance_usd": sum(float(a["balance"]) for a in active_accounts if a.get("currency") == "USD"),
        "transaction_volume_30d": sum(float(t["amount"]) for t in tx_data if t.get("status") == "completed"),
        "active_loans": len(loan_data),
        "active_loan_amount": sum(float(l["amount"]) for l in loan_data),
    }, indent=2, default=str)


@mcp.tool()
def get_customer_risk_profile(customer_id: str) -> str:
    """Evaluate customer risk profile based on transactions, loans, and account status"""
    customer_data = supabase.table("customers").select("*").eq("id", customer_id).execute()
    if not customer_data.data:
        return json.dumps({"error": "Customer not found"})
    customer = customer_data.data[0]
    accounts = supabase.table("accounts").select("*").eq("customer_id", customer_id).execute()
    transactions = (
        supabase.table("transactions")
        .select("amount, status")
        .gte("created_at", (datetime.utcnow() - timedelta(days=90)).isoformat())
        .execute()
    )
    loans = supabase.table("loans").select("*").eq("customer_id", customer_id).execute()

    tx_data = transactions.data if transactions.data else []
    total_tx = len(tx_data)
    failed_tx = sum(1 for t in tx_data if t.get("status") == "failed")
    failure_rate = failed_tx / total_tx if total_tx > 0 else 0
    high_val_tx = sum(1 for t in tx_data if abs(float(t.get("amount", 0))) > 50000)
    has_frozen = any(a.get("status") == "frozen" for a in (accounts.data or []))
    has_defaulted = any(l.get("status") == "defaulted" for l in (loans.data or []))

    risk_factors = []
    if customer.get("status") == "fraud":
        risk_factors.append("Flagged as fraud")
    if customer.get("status") == "suspended":
        risk_factors.append("Suspended")
    if has_frozen:
        risk_factors.append("Has frozen accounts")
    if failure_rate > 0.3:
        risk_factors.append("High transaction failure rate")
    if has_defaulted:
        risk_factors.append("Has defaulted loans")
    if high_val_tx > 5:
        risk_factors.append("Multiple high-value transactions")

    if len(risk_factors) >= 3 or customer.get("status") == "fraud":
        risk_level = "high"
    elif len(risk_factors) >= 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return json.dumps({
        "customer_id": customer_id,
        "customer_name": f"{customer.get('first_name', '')} {customer.get('last_name', '')}",
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "metrics": {
            "total_accounts": len(accounts.data or []),
            "frozen_accounts": sum(1 for a in (accounts.data or []) if a.get("status") == "frozen"),
            "transaction_failure_rate": round(failure_rate, 2),
            "defaulted_loans": sum(1 for l in (loans.data or []) if l.get("status") == "defaulted"),
        },
    }, indent=2, default=str)


def create_app():
    app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Mount("/mcp", app=mcp.sse_app()),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
