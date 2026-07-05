#!/usr/bin/env python3
"""check_balances.py -- summarise cumulative account & project balances.

For every ledgers/<year>.csv it writes ledgers/<year>_balances.csv holding the
running balances *through the end of that year* (balances accumulate from the
lowest year to the highest):

  * every account whose balance is non-zero, and
  * every project that has appeared,

with one line per currency.  A positive balance (Debit > Credit) is shown in the
Debit column, a negative one in the Credit column.

Before writing, it lists every changed balance (USD is the default currency and
shown without a label; other currencies are labelled) and any entries carrying
both a project and a tag, then asks for a single confirmation.  Byte-identical
files are left untouched.

Usage:  python3 check_balances.py
"""

from __future__ import annotations

import io
import os
import sys
from collections import defaultdict

import pandas as pd

import ledger_utils as lu

EPS = 0.005  # treat |balance| below this (half a cent) as zero


def build_rows(year, accounts, projects, categories):
    """Turn the running {(name, currency): net} maps into output rows.

    Rows are sorted high-to-low priority by the order of the ledger columns,
    then alphabetically within each column.
    """
    rows = []

    def emit(net, **cols):
        row = {c: "" for c in lu.COLUMNS}
        row["Date"] = f"{year}1231"
        row.update(cols)
        if net >= 0:
            row["Debit"] = lu.fmt_amount(net)
        else:
            row["Credit"] = lu.fmt_amount(-net)
        rows.append(row)

    for (name, cur), net in accounts.items():
        net = round(net, 2)
        if abs(net) < EPS:
            continue  # accounts: only the non-zero ones
        emit(net, Currency=cur, Account=name)

    for (name, cur), net in projects.items():
        emit(round(net, 2), Currency=cur, Project=name)  # projects: all of them

    for (name, cur), net in categories.items():
        net = round(net, 2)
        if abs(net) < EPS:
            continue  # double-entry categories are expected to net to zero
        emit(net, Currency=cur, Category=name,
             Description="Non-zero double-entry balance!")

    # Generalized ordering: prioritise by column order, then alphabetically.
    rows.sort(key=lambda r: [r[c] for c in lu.COLUMNS])
    return rows


def render_csv(rows):
    df = pd.DataFrame(rows, columns=lu.COLUMNS)
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    # Match the BOM used by the source ledgers so files look consistent.
    return "﻿" + buf.getvalue()


def parse_balance_text(text):
    """Parse a balances csv into {(kind, name, currency): 'Debit x'/'Credit x'}."""
    if text.startswith("﻿"):
        text = text[1:]
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    out = {}
    for _, r in df.iterrows():
        for kind in ("Account", "Project", "Category"):
            if r.get(kind, ""):
                name = r[kind]
                break
        else:
            kind, name = "Account", ""
        debit, credit = r.get("Debit", ""), r.get("Credit", "")
        val = f"Dr {debit}" if debit else f"Cr {credit}"
        out[(kind, name, r.get("Currency", ""))] = val
    return out


def changed_entries(old_text, new_text):
    """Return (name, currency) pairs whose balance differs between two texts."""
    old = parse_balance_text(old_text)
    new = parse_balance_text(new_text)
    changed = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            _kind, name, cur = key
            changed.append((name, cur))
    return changed


def fmt_item(name, cur):
    """USD is the default currency and is listed without a label."""
    return name if cur == "USD" else f"{name} ({cur})"


def main():
    files = lu.ledger_files()
    if not files:
        lu.eprint("No ledgers/<year>.csv files found.")
        return 1

    # Running cumulative nets (Debit - Credit) across all years so far.
    accounts = defaultdict(float)
    projects = defaultdict(float)
    categories = defaultdict(float)  # double-entry categories only

    pending = []          # (out_path, new_text) for files that need writing
    changed = {}          # (name, currency) -> True, unioned across all files
    double_dates = []     # dates of entries carrying both a project and a tag

    for year, path in files:  # ascending -> balances accumulate low->high
        df = lu.read_ledger(path)
        for _, r in df.iterrows():
            net = r["debit"] - r["credit"]
            accounts[(r["Account"], r["Currency"])] += net
            if r["Project"]:
                projects[(r["Project"], r["Currency"])] += net
            if r["Category"] in lu.DOUBLE_ENTRY_CATEGORIES:
                categories[(r["Category"], r["Currency"])] += net

        both = df[(df["Project"] != "") & (df["Tag"] != "")]
        double_dates.extend(both["date"].dt.strftime("%Y-%m-%d").tolist())

        rows = build_rows(year, accounts, projects, categories)
        new_text = render_csv(rows)
        out_path = os.path.join(lu.LEDGER_DIR, f"{year}_balances.csv")

        old_text = ""
        if os.path.exists(out_path):
            with open(out_path, "r", encoding="utf-8") as fh:
                old_text = fh.read()
            if old_text == new_text:
                continue  # up to date, nothing to write
        for key in changed_entries(old_text, new_text):
            changed[key] = True
        pending.append((out_path, new_text))

    # --- Present every warning before writing anything ---
    if changed:
        items = sorted(changed, key=lambda kc: (kc[0], kc[1] != "USD", kc[1]))
        print("Balance changes: " + ", ".join(fmt_item(n, c) for n, c in items))
    if double_dates:
        print("Double-counted entries: " + ", ".join(sorted(set(double_dates))))

    if not pending:
        print("All balance files are up to date.")
        return 0

    try:
        ans = input(f"Write {len(pending)} balance file(s)? [y/N] ").strip().lower()
    except EOFError:
        ans = ""
    if ans not in ("y", "yes"):
        print("No files written.")
        return 0

    for out_path, new_text in pending:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print(f"{os.path.basename(out_path)}: written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
