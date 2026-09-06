"""Dividend received history: sync OpenD cash-flow days into local cache."""

from __future__ import annotations

from datetime import date

from momo.adapters import cash_flow as cash_flow_adapter
from momo.adapters import orders as orders_adapter
from momo.config import get_settings
from momo.db import repo
from momo.db.session import get_session
from momo.opend_client import OpenDError

# One page load / sync click stays under the 20/30s OpenD burst limit.
_SYNC_BATCH = 18


def get_dividend_history(
    *,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
    sync: bool = True,
    show_all: bool = False,
    sync_limit: int = _SYNC_BATCH,
) -> dict:
    settings = get_settings()
    if acc_id is None and settings.trd_acc_id:
        acc_id = settings.trd_acc_id
    env = (trd_env or settings.orders_trd_env).strip().upper()
    if env not in ("REAL", "SIMULATE"):
        env = settings.orders_trd_env

    end_d = cash_flow_adapter.parse_month_end(end) or date.today()
    start_d = cash_flow_adapter.parse_month_start(start) or date(
        end_d.year - 1, end_d.month, 1
    )
    if start_d > end_d:
        start_d, end_d = (
            cash_flow_adapter.parse_month_start(end_d.isoformat()) or end_d,
            cash_flow_adapter.parse_month_end(start_d.isoformat()) or start_d,
        )
    # Clearing dates in the future are empty and waste the OpenD rate budget.
    today = date.today()
    if end_d > today:
        end_d = today
    if start_d > end_d:
        start_d = end_d

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
            "new_dividends": [],
            "totals": _empty_totals(),
            "sync": {
                "fetched_days": 0,
                "remaining_days": 0,
                "accounts_synced": 0,
                "fetched_dates": [],
            },
            "show_all": show_all,
            "auto_sync": False,
            "error": f"No account {acc_id} for {env}",
        }

    sync_info = {
        "fetched_days": 0,
        "remaining_days": 0,
        "accounts_synced": 0,
        "fetched_dates": [],
        "new_dividends": [],
    }
    error = None
    # Sync still uses the from/to range; show_all only widens the cache listing.
    if selected:
        try:
            sync_info = _sync_missing(
                accounts=selected,
                trd_env=env,
                start=start_d,
                end=end_d,
                limit=sync_limit if sync else 0,
            )
        except OpenDError as exc:
            error = str(exc)

    new_dividends = list(sync_info.pop("new_dividends", []))
    new_ids = {(d["acc_id"], d["cashflow_id"]) for d in new_dividends}
    rate_limited = bool(error and "high frequency" in error.lower())
    auto_sync = bool(
        sync
        and selected
        and not (error and not rate_limited)
        and (sync_info.get("remaining_days", 0) > 0 or rate_limited)
    )

    session = get_session()
    try:
        acc_key = str(acc_id) if acc_id else None
        rows = repo.list_dividends(
            session,
            trd_env=env,
            start=None if show_all else start_d.isoformat(),
            end=None if show_all else end_d.isoformat(),
            acc_id=acc_key,
        )
        dividends = [_row_to_dict(r) for r in rows]
        for d in dividends:
            d["is_new"] = (d["acc_id"], d["cashflow_id"]) in new_ids
        resolved = _fill_from_holdings(
            dividends, new_dividends, accounts=selected, trd_env=env
        )
        if resolved:
            repo.upsert_dividends(session, resolved)
    finally:
        session.close()

    return {
        "trd_env": env,
        "accounts": accounts,
        "selected_acc_id": acc_id,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "show_all": show_all,
        "as_of": date.today().isoformat(),
        "dividends": dividends,
        "new_dividends": new_dividends,
        "totals": _totals(dividends),
        "sync": sync_info,
        "auto_sync": auto_sync,
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
    """Pull up to `limit` missing weekday clearing dates (newest first in range)."""
    weekdays = list(reversed(cash_flow_adapter.weekday_range(start, end)))
    start_s, end_s = start.isoformat(), end.isoformat()
    budget = max(0, limit)
    fetched = 0
    remaining = 0
    accounts_synced = 0
    fetched_dates: list[str] = []
    new_dividends: list[dict] = []
    skipped_accounts: list[str] = []

    session = get_session()
    try:
        for acc in accounts:
            aid = str(acc["acc_id"])
            if budget <= 0:
                synced = repo.list_synced_cash_flow_days(
                    session, acc_id=aid, trd_env=trd_env, start=start_s, end=end_s
                )
                remaining += sum(1 for d in weekdays if d.isoformat() not in synced)
                continue

            synced = repo.list_synced_cash_flow_days(
                session, acc_id=aid, trd_env=trd_env, start=start_s, end=end_s
            )
            missing = [d for d in weekdays if d.isoformat() not in synced]
            remaining += max(0, len(missing) - budget)
            batch = missing[:budget]
            if not batch:
                continue

            try:
                by_day = cash_flow_adapter.fetch_cash_flow_days(
                    clearing_dates=[d.isoformat() for d in batch],
                    acc_id=acc["acc_id"],
                    trd_env=trd_env,
                )
            except OpenDError as exc:
                if not cash_flow_adapter.account_supports_cash_flow(exc):
                    skipped_accounts.append(aid)
                    remaining -= max(0, len(missing) - budget)
                    continue
                raise

            batch_dividends: list[dict] = []
            for day, rows in by_day.items():
                for row in rows:
                    if not cash_flow_adapter.is_dividend_row(row):
                        continue
                    enriched = cash_flow_adapter.enrich_dividend(row)
                    batch_dividends.append(
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

            if batch_dividends:
                repo.upsert_dividends(session, batch_dividends)
                new_dividends.extend(batch_dividends)
            day_strs = [d.isoformat() for d in batch]
            repo.mark_cash_flow_days_synced(
                session,
                acc_id=aid,
                trd_env=trd_env,
                clearing_dates=day_strs,
            )
            fetched_dates.extend(day_strs)
            fetched += len(batch)
            budget -= len(batch)
            accounts_synced += 1
    finally:
        session.close()

    new_dividends.sort(key=lambda d: d.get("clearing_date") or "", reverse=True)
    return {
        "fetched_days": fetched,
        "remaining_days": remaining,
        "accounts_synced": accounts_synced,
        "fetched_dates": fetched_dates,
        "new_dividends": new_dividends,
        "skipped_accounts": skipped_accounts,
    }


def _fill_from_holdings(
    dividends: list[dict],
    new_dividends: list[dict],
    *,
    accounts: list[dict],
    trd_env: str,
) -> list[dict]:
    """Resolve remark-less stock rows from a unique open holding; persist those."""
    needs = [
        d
        for d in dividends
        if not d.get("stock_code")
        and not d.get("stock_name")
        and d.get("cashflow_remark")
        and "fund cash dividend" not in str(d.get("cashflow_remark") or "").lower()
    ]
    if not needs or not accounts:
        return []

    positions: list[dict] = []
    try:
        for acc in accounts:
            positions.extend(
                orders_adapter.list_positions(acc["acc_id"], trd_env=trd_env)
            )
    except OpenDError:
        return []
    if not positions:
        return []

    resolved: list[dict] = []
    by_id = {(d["acc_id"], d["cashflow_id"]): d for d in new_dividends}
    for d in needs:
        filled = cash_flow_adapter.attach_holding(d, positions)
        if not filled.get("stock_code") and not filled.get("stock_name"):
            continue
        d["stock_code"] = filled.get("stock_code") or ""
        d["stock_name"] = filled.get("stock_name") or ""
        if d.get("shares") is None:
            d["shares"] = filled.get("shares")
        twin = by_id.get((d["acc_id"], d["cashflow_id"]))
        if twin is not None:
            twin["stock_code"] = d["stock_code"]
            twin["stock_name"] = d["stock_name"]
            if twin.get("shares") is None:
                twin["shares"] = d.get("shares")
        resolved.append(d)
    return resolved


def _row_to_dict(row) -> dict:
    base = {
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
        "stock_code": row.stock_code or "",
        "stock_name": row.stock_name or "",
        "shares": row.shares,
    }
    # Re-parse remark so older cache rows pick up parser improvements.
    if base["cashflow_remark"] and (
        (not base["stock_code"] and not base["stock_name"]) or base["shares"] is None
    ):
        parsed = cash_flow_adapter.enrich_dividend(base)
        if not base["stock_code"] and not base["stock_name"]:
            base["stock_code"] = parsed.get("stock_code") or ""
            base["stock_name"] = parsed.get("stock_name") or ""
        if base["shares"] is None:
            base["shares"] = parsed.get("shares")
    return base


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
