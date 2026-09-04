"""Safe helpers for files uploaded through the Streamlit sidebar.

The web interface receives a browser filename, which must never be trusted as a
filesystem path.  These small functions validate the ticker and extension,
discard any directory components, and write only below ``data/private``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

UploadKind = Literal["screener", "company_document"]

ALLOWED_TICKERS = {"PNGJL", "KALYANKJIL", "SENCO", "THANGAMAYL"}
ALLOWED_SUFFIXES = {
    "screener": {".csv", ".xlsx"},
    "company_document": {".pdf", ".txt", ".csv"},
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def save_private_upload(
    *,
    root: Path,
    ticker: str,
    original_name: str,
    content: bytes,
    kind: UploadKind,
) -> Path:
    """Validate and persist one user-selected evidence file.

    Screener exports receive a predictable ``<TICKER>.<extension>`` filename so
    the source collector can match each workbook to the right company.  Other
    documents keep a cleaned version of their original name inside a
    ticker-specific directory.
    """

    normalized_ticker = ticker.upper().strip()
    if normalized_ticker not in ALLOWED_TICKERS:
        raise ValueError(f"Unsupported comparison ticker: {ticker!r}.")
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The uploaded file is larger than the 25 MB safety limit.")

    # Path(...).name removes any browser-supplied directory components.  The
    # regular expression then leaves a portable filename made from simple
    # letters, digits, dots, underscores, and dashes.
    browser_name = Path(original_name).name
    suffix = Path(browser_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES[kind]:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES[kind]))
        raise ValueError(f"{kind} uploads must use one of: {allowed}.")

    root = root.expanduser().resolve()
    if kind == "screener":
        destination = root / f"{normalized_ticker}{suffix}"
    else:
        cleaned_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(browser_name).stem).strip("-.")
        cleaned_stem = cleaned_stem or "document"
        destination = root / normalized_ticker / f"{cleaned_stem}{suffix}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return destination
