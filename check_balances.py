#!/usr/bin/env python3
"""check_balances.py -- summarise cumulative account & project balances.

For every ledgers/<year>.csv it writes ledgers/<year>_balances.csv holding the
running balances *through the end of that year* (balances accumulate from the
lowest year to the highest):

  * every account whose balance is non-zero, and
  * every project that has appeared,

with one line per currency.  A positive balance (Debit > Credit) is shown in the
Debit column, a negative one in the Credit column.

Existing balance files are left untouched when byte-identical; when they differ,
the differences are listed and the user is asked before overwriting.

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


def build_rows(year, accounts, projects):
    """Turn the running {(name, currency): net} maps into output rows.

    Rows are sorted by priority (highest first): currency, then account before
    project, then alphabetically by name.
    """
    # Collect (currency, kind_order, name_col, name, net) tuples, where
    # kind_order 0 = Account, 1 = Project so accounts sort before projects.
    entries = []

    for (name, cur), net in accounts.items():
        if abs(round(net, 2)) < EPS:
            continue  # accounts: only the non-zero ones
        entries.append((cur, 0, "Account", name, round(net, 2)))

    for (name, cur), net in projects.items():
        entries.append((cur, 1, "Project", name, round(net, 2)))  # all projects

    rows = []
    for cur, _kind, name_col, name, net in sorted(entries, key=lambda e: (e[0], e[1], e[3])):
        row = {c: "" for c in lu.COLUMNS}
        row["Date"] = f"{year}1231"
        row["Currency"] = cur
        row[name_col] = name
        if net >= 0:
            row["Debit"] = lu.fmt_amount(net)
        else:
            row["Credit"] = lu.fmt_amount(-net)
        rows.append(row)

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
        kind = "Account" if r.get("Account", "") else "Project"
        name = r.get("Account", "") or r.get("Project", "")
        debit, credit = r.get("Debit", ""), r.get("Credit", "")
        val = f"Dr {debit}" if debit else f"Cr {credit}"
        out[(kind, name, r.get("Currency", ""))] = val
    return out


def diff_balances(old_text, new_text):
    old = parse_balance_text(old_text)
    new = parse_balance_text(new_text)
    lines = []
    for key in sorted(set(old) | set(new)):
        o, n = old.get(key), new.get(key)
        if o == n:
            continue
        kind, name, cur = key
        label = f"{kind} {name} [{cur}]"
        if o is None:
            lines.append(f"  + {label}: {n}")
        elif n is None:
            lines.append(f"  - {label}: {o} (gone)")
        else:
            lines.append(f"  ~ {label}: {o} -> {n}")
    return lines


def write_with_confirmation(path, new_text):
    name = os.path.basename(path)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            old_text = fh.read()
        if old_text == new_text:
            print(f"{name}: unchanged.")
            return
        print(f"{name}: differs from computed balances:")
        for line in diff_balances(old_text, new_text):
            print(line)
        try:
            ans = input(f"Overwrite {name}? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print(f"{name}: kept existing file.")
            return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    print(f"{name}: written.")


def main():
    files = lu.ledger_files()
    if not files:
        lu.eprint("No ledgers/<year>.csv files found.")
        return 1

    # Running cumulative nets (Debit - Credit) across all years so far.
    accounts = defaultdict(float)
    projects = defaultdict(float)

    for year, path in files:  # ascending -> balances accumulate low->high
        df = lu.read_ledger(path)
        for _, r in df.iterrows():
            net = r["debit"] - r["credit"]
            accounts[(r["Account"], r["Currency"])] += net
            if r["Project"]:
                projects[(r["Project"], r["Currency"])] += net

        rows = build_rows(year, accounts, projects)
        new_text = render_csv(rows)
        out_path = os.path.join(lu.LEDGER_DIR, f"{year}_balances.csv")
        write_with_confirmation(out_path, new_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
