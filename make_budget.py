#!/usr/bin/env python3
"""make_budget.py -- derive an annualised budget from past ledger activity.

Arguments (each in yyyymm form):
    (none)          the 12-month period ending in the month before the current month
    <start> <end>   from <start> month through <end> month (inclusive)

For the selected span it reports, all converted to USD:
    * income/expense per category, annualised (value / months * 12), excluding
      entries that carry a project or a tag, and excluding the double-entry
      Transfer/Borrowing/Lending categories,
    * income/expense per project (period sum, not annualised),
    * income/expense per tag (period sum, not annualised).
Items whose value rounds to zero are omitted.
Both expense and income are reported as positive figures.

Output:  ledgers/budget_<startYYYYMM>_<endYYYYMM>.csv  (endYYYYMM = the actual
last month analysed).

Usage:  python3 make_budget.py [yyyymm yyyymm]
"""

from __future__ import annotations

import calendar
import os
import sys

import pandas as pd

import ledger_utils as lu


def parse_ym(text):
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"expected yyyymm, got {text!r}")
    y, m = int(text[:4]), int(text[4:])
    if not 1 <= m <= 12:
        raise ValueError(f"bad month in {text!r}")
    return (y, m)


def month_first(ym):
    return pd.Timestamp(ym[0], ym[1], 1)


def month_last(ym):
    return pd.Timestamp(ym[0], ym[1], calendar.monthrange(ym[0], ym[1])[1])


def prev_month(ym):
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)


def add_months(ym, delta):
    idx = ym[0] * 12 + (ym[1] - 1) + delta
    return (idx // 12, idx % 12 + 1)


def months_between(start, end):
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


def ym_str(ym):
    return f"{ym[0]:04d}{ym[1]:02d}"


def legend_categories():
    """All budgetable categories from the template legend (double-entry removed)."""
    path = os.path.join(lu.LEDGER_DIR, "template.csv")
    cats = []
    if os.path.exists(path):
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        for c in df["Category"]:
            c = c.strip()
            if c and c not in lu.DOUBLE_ENTRY_CATEGORIES and c not in cats:
                cats.append(c)
    return cats


def current_month():
    """The current calendar month as a (year, month) pair."""
    now = pd.Timestamp.now()
    return (now.year, now.month)


def resolve_range(args, all_entries):
    """Return (start_ym, end_ym) for analysis."""
    if len(args) == 0:
        # 12-month period ending in the month before the current month.
        end_ym = prev_month(current_month())
        start_ym = add_months(end_ym, -11)
    else:
        start_ym, end_ym = parse_ym(args[0]), parse_ym(args[1])

    if months_between(start_ym, end_ym) < 1:
        raise SystemExit("Analysis range is empty (start month is after end month).")
    return start_ym, end_ym


def annualise(series_net, months):
    return series_net / months * 12.0


def main(argv):
    args = argv[1:]
    if len(args) not in (0, 2):
        raise SystemExit(__doc__)

    all_entries = lu.load_all_entries()
    if all_entries.empty:
        raise SystemExit("No ledger entries found.")

    start_ym, end_ym = resolve_range(args, all_entries)
    df = all_entries[(all_entries["date"] >= month_first(start_ym)) &
                     (all_entries["date"] <= month_last(end_ym))].copy()
    if df.empty:
        raise SystemExit("No entries in the selected range.")

    months = months_between(start_ym, end_ym)
    df = lu.add_usd(df)
    df["expense_usd"] = df["credit_usd"] - df["debit_usd"]  # expense +, income -

    rows = []

    # --- Categories: exclude project/tag rows and double-entry categories ---
    cat_mask = ((df["Project"] == "") & (df["Tag"] == "") &
                (~df["Category"].isin(lu.DOUBLE_ENTRY_CATEGORIES)))
    cat_net = df[cat_mask].groupby("Category")["expense_usd"].sum()
    for cat in legend_categories():
        annual = annualise(cat_net.get(cat, 0.0), months)
        if cat in lu.INCOME_CATEGORIES:
            annual = -annual  # report income as a positive budget figure
        annual = round(annual, 2)
        if annual != 0.0:  # omit zero-value items
            rows.append(("category", cat, annual))

    # --- Projects: period sum, not annualised ---
    proj = df[df["Project"] != ""]
    for name, net in proj.groupby("Project")["expense_usd"].sum().items():
        net = round(net, 2)
        if net != 0.0:
            rows.append(("project", name, net))

    # --- Tags: period sum, not annualised ---
    tag = df[df["Tag"] != ""]
    for name, net in tag.groupby("Tag")["expense_usd"].sum().items():
        net = round(net, 2)
        if net != 0.0:
            rows.append(("tag", name, net))

    out = pd.DataFrame(rows, columns=["Type", "Name", "Annual"])
    out.insert(2, "Currency", "USD")

    fname = f"budget_{ym_str(start_ym)}_{ym_str(end_ym)}.csv"
    out_path = os.path.join(lu.LEDGER_DIR, fname)
    out.to_csv(out_path, index=False)

    # Console summary
    print(f"Analysed {ym_str(start_ym)}..{ym_str(end_ym)} ({months} months), "
          f"{len(df)} entries, USD. Categories annualised; projects/tags summed.")
    print(f"Wrote ledgers/{fname}\n")
    for typ in ("category", "project", "tag"):
        sub = [r for r in rows if r[0] == typ]
        if not sub:
            continue
        print(f"[{typ}]")
        for _, name, amt in sorted(sub, key=lambda r: -abs(r[2])):
            kind = "income " if (typ == "category" and name in lu.INCOME_CATEGORIES) else "expense"
            print(f"  {kind} {amt:>12,.2f}  {name}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
