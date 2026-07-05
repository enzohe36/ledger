"""Shared helpers for the ledger project.

Data model (columns of every ledgers/*.csv):
    Date,Currency,Account,Category,Project,Tag,Debit,Credit,Description

Conventions
-----------
* The first rows of a ledger have an empty ``Date`` -- they are the category
  legend and are skipped for every computation.
* ``Debit`` and ``Credit`` are non-negative-by-intent but may be negative
  (corrections).  Blank means 0.
* Balance sense (assets):   net   = Debit - Credit   (money you have / are owed)
* P&L sense (budgeting):    spend = Credit - Debit   (money leaving = expense +,
                                                       money arriving = income -)
* All amounts are converted to USD via ECB daily rates (Frankfurter API),
  cached per currency in currencies/<CUR>.csv.
"""

from __future__ import annotations

import os
import re
import sys
import datetime as dt

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.join(ROOT, "ledgers")
CURRENCY_DIR = os.path.join(ROOT, "currencies")

COLUMNS = ["Date", "Currency", "Account", "Category", "Project", "Tag",
           "Debit", "Credit", "Description"]

# Categories that only move money between accounts / people -- excluded from
# budget income/expense (they net to ~zero and are not real P&L).
DOUBLE_ENTRY_CATEGORIES = {"Transfer", "Borrowing", "Lending"}

# Income categories (money in).  Everything else that is not double-entry is an
# expense category.  Income is reported as a positive figure (Debit - Credit),
# expense as a positive figure (Credit - Debit).
INCOME_CATEGORIES = {"Salary", "Investment"}

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{start}..{end}"


# --------------------------------------------------------------------------- #
# Ledger discovery / loading
# --------------------------------------------------------------------------- #
_YEAR_RE = re.compile(r"(\d{4})\.csv$")


def ledger_files():
    """Return [(year, path), ...] for ledgers/<year>.csv, sorted by year.

    Excludes template.csv and generated *_balances.csv files.
    """
    out = []
    for name in os.listdir(LEDGER_DIR):
        m = _YEAR_RE.fullmatch(name)
        if m:
            out.append((int(m.group(1)), os.path.join(LEDGER_DIR, name)))
    return sorted(out)


def read_ledger(path):
    """Read one ledger csv into a DataFrame of real entries (legend dropped).

    Adds numeric ``debit``/``credit`` columns and a parsed ``date`` column.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    # Guard against unexpected columns / ordering.
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[df["Date"].str.strip() != ""].copy()
    df["date"] = pd.to_datetime(df["Date"].str.strip(), format="%Y-%m-%d")
    df["debit"] = _to_num(df["Debit"])
    df["credit"] = _to_num(df["Credit"])
    for col in ("Currency", "Account", "Category", "Project", "Tag"):
        df[col] = df[col].str.strip()
    return df


def _to_num(series):
    return pd.to_numeric(series.str.strip().replace("", "0"), errors="coerce").fillna(0.0)


def load_all_entries():
    """Concatenate every yearly ledger into one DataFrame."""
    frames = [read_ledger(p) for _, p in ledger_files()]
    if not frames:
        return pd.DataFrame(columns=COLUMNS + ["date", "debit", "credit"])
    return pd.concat(frames, ignore_index=True)


def fmt_amount(x):
    """Format a money amount the way the ledgers do (2 dp, no trailing zeros lost)."""
    if x == 0:
        return "0.00"
    return f"{x:.2f}"


# --------------------------------------------------------------------------- #
# Currency conversion (Frankfurter / ECB, cached in currencies/<CUR>.csv)
# --------------------------------------------------------------------------- #
_rate_cache = {}  # currency -> pd.Series indexed by date (USD per 1 unit)


def _rate_path(currency):
    return os.path.join(CURRENCY_DIR, f"{currency}.csv")


def _load_rate_file(currency):
    path = _rate_path(currency)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"Date": str})
    df["date"] = pd.to_datetime(df["Date"], format="%Y-%m-%d")
    return df.set_index("date")["Rate"].sort_index()


def _fetch_rates(currency, start, end):
    """Fetch daily <currency>->USD rates for [start, end] from Frankfurter."""
    url = FRANKFURTER_URL.format(start=start.isoformat(), end=end.isoformat())
    resp = requests.get(url, params={"base": currency, "symbols": "USD"}, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("rates", {})
    rows = {pd.to_datetime(d): r["USD"] for d, r in data.items() if "USD" in r}
    return pd.Series(rows).sort_index()


def ensure_rates(currency, need_start, need_end):
    """Guarantee currencies/<CUR>.csv covers [need_start, need_end]; return series.

    Rates are ECB business-day rates; missing days (weekends/holidays) are
    forward-filled at lookup time.  The cache file is (re)fetched only when it
    is absent or does not already cover the requested span.
    """
    if currency == "USD":
        return None

    series = _load_rate_file(currency)
    need_start = pd.Timestamp(need_start)
    need_end = pd.Timestamp(need_end)

    covered = (series is not None
               and series.index.min() <= need_start
               and series.index.max() >= need_end)
    if not covered:
        # Fetch a generous span so we rarely re-hit the network.
        fetch_start = need_start
        fetch_end = need_end
        if series is not None:
            fetch_start = min(fetch_start, series.index.min())
            fetch_end = max(fetch_end, series.index.max())
        fresh = _fetch_rates(currency, fetch_start.date(), fetch_end.date())
        if series is not None:
            series = pd.concat([series, fresh])
            series = series[~series.index.duplicated(keep="last")].sort_index()
        else:
            series = fresh
        os.makedirs(CURRENCY_DIR, exist_ok=True)
        out = series.rename("Rate").reset_index()
        out.columns = ["Date", "Rate"]
        out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
        out.to_csv(_rate_path(currency), index=False)

    _rate_cache[currency] = series
    return series


def _rate_on(currency, date):
    series = _rate_cache.get(currency)
    if series is None:
        series = _load_rate_file(currency)
        _rate_cache[currency] = series
    if series is None or len(series) == 0:
        raise RuntimeError(f"No cached rates for {currency}; call ensure_rates first.")
    date = pd.Timestamp(date)
    # Most recent rate on or before `date` (forward-fill business-day gaps).
    pos = series.index.searchsorted(date, side="right") - 1
    if pos < 0:
        pos = 0  # date precedes first known rate -> use earliest available
    return float(series.iloc[pos])


def prepare_rates(df):
    """Fetch/cache every non-USD currency needed by the entries in ``df``."""
    for cur in sorted(set(df["Currency"]) - {"USD", ""}):
        sub = df[df["Currency"] == cur]
        ensure_rates(cur, sub["date"].min().date(), sub["date"].max().date())


def latest_entry_ledger():
    """Return (year, path) of the highest-year ledger that has real entries."""
    for year, path in reversed(ledger_files()):
        if not read_ledger(path).empty:
            return year, path
    raise RuntimeError("No ledger contains any entries.")


def add_usd(df):
    """Return a copy of ``df`` with debit_usd / credit_usd columns."""
    df = df.copy()
    if df.empty:
        df["debit_usd"] = df["debit"]
        df["credit_usd"] = df["credit"]
        return df
    prepare_rates(df)

    def factor(row):
        return 1.0 if row["Currency"] in ("USD", "") else _rate_on(row["Currency"], row["date"])

    f = df.apply(factor, axis=1)
    df["debit_usd"] = df["debit"] * f
    df["credit_usd"] = df["credit"] * f
    return df


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)
