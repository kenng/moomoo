"""Google Sheets I/O via a service-account credentials file."""

from __future__ import annotations

from pathlib import Path

from momo.config import get_settings

_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


class GoogleSheetsError(Exception):
    """Google Sheets auth, config, or API error."""


def replace_tab_values(
    *,
    headers: list[str],
    rows: list[list],
    spreadsheet_id: str | None = None,
    tab_title: str | None = None,
    credentials_path: Path | str | None = None,
) -> dict:
    """Clear a worksheet and write header + data rows. Creates the tab if missing."""
    settings = get_settings()
    sheet_id = (spreadsheet_id or settings.google_sheets_spreadsheet_id or "").strip()
    if not sheet_id:
        raise GoogleSheetsError("GOOGLE_SHEETS_SPREADSHEET_ID is not set")

    title = (tab_title or settings.google_sheets_stock_orders_tab or "stock orders").strip()
    if not title:
        raise GoogleSheetsError("worksheet tab title is empty")

    path = Path(credentials_path or settings.google_sheets_credentials_path)
    if not path.is_file():
        raise GoogleSheetsError(f"credentials file not found: {path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise GoogleSheetsError(
            "gspread/google-auth not installed; pip install gspread google-auth"
        ) from exc

    try:
        creds = Credentials.from_service_account_file(str(path), scopes=_SCOPES)
        client = gspread.authorize(creds)
        try:
            spreadsheet = client.open_by_key(sheet_id)
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (403, 404):
                sa_email = getattr(creds, "service_account_email", "") or "the service account"
                raise GoogleSheetsError(
                    f"cannot open spreadsheet {sheet_id!r} ({status}). "
                    f"Check GOOGLE_SHEETS_SPREADSHEET_ID is the full /d/.../edit id, "
                    f"and share the sheet with {sa_email} as Editor."
                ) from exc
            raise
        try:
            worksheet = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=title,
                rows=max(len(rows) + 1, 100),
                cols=max(len(headers), 1),
            )

        values = [headers, *rows]
        worksheet.clear()
        if values:
            worksheet.update(values, "A1", value_input_option="USER_ENTERED")
    except GoogleSheetsError:
        raise
    except Exception as exc:
        raise GoogleSheetsError(str(exc)) from exc

    return {
        "spreadsheet_id": sheet_id,
        "tab": title,
        "rows_written": len(rows),
    }
