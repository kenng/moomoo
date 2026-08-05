"""Dividend received history: sync OpenD cash-flow days into local cache."""

from __future__ import annotations

from datetime import date, timedelta

from momo.adapters import cash_flow as cash_flow_adapter
from momo.config import get_settings
from momo.db import repo
from momo.db.session import get_session
from momo.opend_client import OpenDError

# One page load / sync click stays under the 20/30s OpenD burst limit.
_SYNC_BATCH = 18
_DEFAULT_HISTORY_DAYS = 365


def get_dividend_history(
    *,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
    sync: bool = True,
    sync_limit: int = _SYNC_BATCH,
) -> dict:
    settings = get_settings()
    if acc_id is None and settings.trd_acc_id:
        acc_id = settings.trd_acc_id
    env = (trd_env or settings.orders_trd_env).strip().upper()
    if env not in ("REAL", "SIMULATE"):
        env = settings.orders_trd_env

    end_d = cash_flow_adapter.parse_day(end) or date.today()
    start_d = cash_flow_adapter.parse_day(start) or (
        end_d - timedelta(days=_DEFAULT_HISTORY_DAYS - 1)
    )
    if start_d > end_d:
        start_d, end_d = end_d, start_d

    accounts = cash_flow_adapter.list_accounts(trd_env=env)
    selected = [a for a in accounts if a["acc_id"] == acc_id] if acc_id else accounts
    if acc_id and not selected:
        return {
            "trd_env": env,
            "accounts": accounts,
            "selected_acc_id": acc_id,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "as_of": date.today().isoformat(),
            "dividends": [],
            "totals": _empty_totals(),
            "sync": {"fetched_days": 0, "remaining_days": 0, "accounts_synced": 0},
            "error": f"No account {acc_id} for {env}",
        }

    sync_info = {"fetched_days": 0, "remaining_days": 0, "accounts_synced": 0}
    error = None
    if sync and selected:
        try:
            sync_info = _sync_missing(
                accounts=selected,
                trd_env=env,
                start=start_d,
                end=end_d,
                limit=sync_limit,
            )
        except OpenDError as exc:
            error = str(exc)

    session = get_session()
    try:
        acc_key = str(acc_id) if acc_id else None
        rows = repo.list_dividends(
            session,
            trd_env=env,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            acc_id=acc_key,
        )
        dividends = [_row_to_dict(r) for r in rows]
    finally:
        session.close()

    return {
        "trd_env": env,
        "accounts": accounts,
        "selected_acc_id": acc_id,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "as_of": date.today().isoformat(),
        "dividends": dividends,
        "totals": _totals(dividends),
        "sync": sync_info,
        "error": error,
    }


def _sync_missing(
    *,
    accounts: list[dict],
    trd_env: str,
    start: date,
    end: date,
    limit: int,
) -> dict:
    """Pull up to `limit` missing weekday clearing dates (newest first)."""
    weekdays = list(reversed(cash_flow_adapter.weekday_range(start, end)))
    start_s, end_s = start.isoformat(), end.isoformat()
    budget = max(0, limit)
    fetched = 0
    remaining = 0
    accounts_synced = 0

    session = get_session()
    try:
        for acc in accounts:
            if budget <= 0:
                # Still count remaining for UI.
                aid = str(acc["acc_id"])
                synced = repo.list_synced_cash_flow_days(
                    session, acc_id=aid, trd_env=trd_env, start=start_s, end=end_s
                )
                remaining += sum(1 for d in weekdays if d.isoformat() not in synced)
                continue

            aid = str(acc["acc_id"])
            synced = repo.list_synced_cash_flow_days(
                session, acc_id=aid, trd_env=trd_env, start=start_s, end=end_s
            )
            missing = [d for d in weekdays if d.isoformat() not in synced]
            remaining += max(0, len(missing) - budget)
            batch = missing[:budget]
            if not batch:
                continue

            by_day = cash_flow_adapter.fetch_cash_flow_days(
                clearing_dates=[d.isoformat() for d in batch],
                acc_id=acc["acc_id"],
                trd_env=trd_env,
            )
            dividends: list[dict] = []
            for day, rows in by_day.items():
                for row in rows:
                    if not cash_flow_adapter.is_dividend_row(row):
                        continue
                    enriched = cash_flow_adapter.enrich_dividend(row)
                    dividends.append(
                        {
                            "acc_id": aid,
                            "trd_env": trd_env,
                            "cashflow_id": enriched["cashflow_id"],
                            "clearing_date": enriched["clearing_date"] or day,
                            "settlement_date": enriched.get("settlement_date") or "",
                            "currency": enriched.get("currency") or "",
                            "cashflow_type": enriched.get("cashflow_type") or "",
                            "cashflow_direction": enriched.get("cashflow_direction")
                            or "",
                            "cashflow_amount": enriched.get("cashflow_amount"),
                            "cashflow_remark": enriched.get("cashflow_remark") or "",
                            "stock_code": enriched.get("stock_code") or "",
                            "stock_name": enriched.get("stock_name") or "",
                            "shares": enriched.get("shares"),
                        }
                    )

            if dividends:
                repo.upsert_dividends(session, dividends)
            repo.mark_cash_flow_days_synced(
                session,
                acc_id=aid,
                trd_env=trd_env,
                clearing_dates=[d.isoformat() for d in batch],
            )
            fetched += len(batch)
            budget -= len(batch)
            accounts_synced += 1
    finally:
        session.close()

    return {
        "fetched_days": fetched,
        "remaining_days": remaining,
        "accounts_synced": accounts_synced,
    }


def _row_to_dict(row) -> dict:
    return {
        "acc_id": row.acc_id,
        "trd_env": row.trd_env,
        "cashflow_id": row.cashflow_id,
        "clearing_date": row.clearing_date,
        "settlement_date": row.settlement_date,
        "currency": row.currency,
        "cashflow_type": row.cashflow_type,
        "cashflow_direction": row.cashflow_direction,
        "cashflow_amount": row.cashflow_amount,
        "cashflow_remark": row.cashflow_remark,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "shares": row.shares,
    }


def _totals(dividends: list[dict]) -> dict:
    by_ccy: dict[str, float] = {}
    for d in dividends:
        amt = d.get("cashflow_amount")
        if amt is None:
            continue
        ccy = d.get("currency") or "?"
        by_ccy[ccy] = by_ccy.get(ccy, 0.0) + float(amt)
    return {
        "count": len(dividends),
        "by_currency": [
            {"currency": ccy, "amount": amt}
            for ccy, amt in sorted(by_ccy.items())
        ],
    }


def _empty_totals() -> dict:
    return {"count": 0, "by_currency": []}
