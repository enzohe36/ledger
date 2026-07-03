#!/usr/bin/env python3
"""check_budge.py -- compare the current ledger against a budget.

Reads the highest-year ledgers/<year>.csv and a budget_*.csv produced by
make_budget.py, then reports (all amounts converted to USD):

  Categories  total income/expense per category (excluding entries with a
              project or tag, and the double-entry categories), the prorated
              budget progress (annual / 12 * months elapsed in the ledger,
              counting the final month even if incomplete), and the gap.
  Projects    total per project vs the full annual budget; projects present in
              the ledger but absent from the budget are flagged.
  Tags        same treatment as projects.

It renders ledgers/<year>_progress.png: one progress box per item, the annual
budget as the divisor.  The filled portion is the actual total; a tick marks the
ideal amount.  Boxes that exceed the annual budget get a red, fully-filled bar.
The number beside each box is (ideal - total): negative means over the ideal.

Usage:  python3 check_budge.py <budget_csv>
"""

from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.text import Text
import pandas as pd

import ledger_utils as lu


def ledger_month_span(df):
    """Number of calendar months covered, counting the final month in full."""
    first, last = df["date"].min(), df["date"].max()
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


def load_budget(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["Annual"] = pd.to_numeric(df["Annual"], errors="coerce").fillna(0.0)
    budgets = {"category": {}, "project": {}, "tag": {}}
    for _, r in df.iterrows():
        budgets.get(r["Type"], {})[r["Name"]] = float(r["Annual"])
    return budgets


def build_items(df, budgets, months):
    """Return per-section lists of item dicts for reporting and plotting."""
    df = df.copy()
    df["expense_usd"] = df["credit_usd"] - df["debit_usd"]  # expense +, income -

    sections = {}

    # Categories: exclude project/tag entries and double-entry categories.
    cat_mask = ((df["Project"] == "") & (df["Tag"] == "") &
                (~df["Category"].isin(lu.DOUBLE_ENTRY_CATEGORIES)))
    cat_tot = df[cat_mask].groupby("Category")["expense_usd"].sum().to_dict()
    sections["category"] = _items(budgets["category"], cat_tot,
                                   ideal_scale=months / 12.0)

    # Projects & tags: full annual budget is the ideal.
    proj_tot = df[df["Project"] != ""].groupby("Project")["expense_usd"].sum().to_dict()
    sections["project"] = _items(budgets["project"], proj_tot, ideal_scale=1.0)

    tag_tot = df[df["Tag"] != ""].groupby("Tag")["expense_usd"].sum().to_dict()
    sections["tag"] = _items(budgets["tag"], tag_tot, ideal_scale=1.0)

    return sections


def _items(budget_map, totals, ideal_scale):
    names = sorted(set(budget_map) | set(totals))
    items = []
    for name in names:
        annual = budget_map.get(name, 0.0)
        total = totals.get(name, 0.0)
        ideal = annual * ideal_scale
        in_budget = name in budget_map
        items.append({
            "name": name, "annual": annual, "total": total,
            "ideal": ideal, "diff": ideal - total, "in_budget": in_budget,
        })
    return items


def _fraction(total, annual):
    if annual != 0:
        return total / annual
    return 1.0 if abs(total) > 1e-9 else 0.0


def _is_income(it):
    ref = it["annual"] if it["annual"] != 0 else it["total"]
    return ref < 0


# Fixed layout constants (inches). Text columns are sized to their content;
# GAP is the constant spacing between every column.
GAP = 0.30         # spacing between columns
PROG_W = 3.20      # progress-bar column width
ROW_H = 0.34       # height of one row
SECTION_GAP = 0.20  # extra vertical space before the header and each section label
MARGIN = SECTION_GAP     # figure margins
FS = HFS = SFS = 14  # item / header / section-label font sizes


def _val_str(it):
    # Denominator is the budget-to-date (prorated for categories, full annual for
    # projects/tags) so that Value and Difference agree: diff == budget - sum.
    return f"{it['total']:,.0f} / {it['ideal']:,.0f}"


def _diff_str(it):
    return f"{it['diff']:+,.0f}"


def render(sections, out_path, title):
    cats = sections["category"]
    ordered = [("Income", [i for i in cats if _is_income(i)]),
               ("Expense", [i for i in cats if not _is_income(i)]),
               ("Projects", sections["project"]),
               ("Tags", sections["tag"])]
    ordered = [(lbl, its) for lbl, its in ordered if its]
    items = [it for _, its in ordered for it in its]

    fig = plt.figure()
    trans = fig.dpi_scale_trans          # 1 unit == 1 inch
    renderer = fig.canvas.get_renderer()

    def text_w(s, size, bold):
        t = Text(0, 0, s, fontsize=size,
                 fontweight="bold" if bold else "normal", transform=trans)
        fig.add_artist(t)
        w = t.get_window_extent(renderer=renderer).width / fig.dpi
        t.remove()
        return w

    def col_w(strings, size, bold):
        return max((text_w(s, size, bold) for s in strings), default=0.0)

    # Measure each text column against its widest cell (header included).
    w_cat = max(text_w("Category", HFS, True),
                col_w([lbl for lbl, _ in ordered], SFS, True),
                col_w([it["name"] for it in items], FS, False))
    w_val = max(text_w("Actual/Budget", HFS, True),
                col_w([_val_str(it) for it in items], FS, False))
    w_diff = max(text_w("Difference", HFS, True),
                 col_w([_diff_str(it) for it in items], FS, False))

    # Column anchors (left edges for left-aligned cols, right edges otherwise).
    x_cat = MARGIN
    x_prog = x_cat + w_cat + GAP
    x_val_r = x_prog + PROG_W + GAP + w_val
    x_diff_r = x_val_r + GAP + w_diff
    total_w = x_diff_r + MARGIN

    n_rows = 1 + sum(1 + len(its) for _, its in ordered)  # header + section labels + items
    title_h = 0.30  # height reserved for the title text
    # MARGIN top/bottom; SECTION_GAP before the header and each section label.
    total_h = (MARGIN + title_h + SECTION_GAP + n_rows * ROW_H
               + len(ordered) * SECTION_GAP + MARGIN)
    fig.set_size_inches(total_w, total_h)

    def add_text(x, y, s, size, ha, bold=False, color="black"):
        fig.add_artist(Text(x, y, s, fontsize=size, ha=ha, va="center",
                            color=color, fontweight="bold" if bold else "normal",
                            transform=trans))

    def seg(x0, y0, x1, y1, **kw):
        fig.add_artist(Line2D([x0, x1], [y0, y1], transform=trans, **kw))

    def rect(x, y, w, h, **kw):
        fig.add_artist(Rectangle((x, y), w, h, transform=trans, **kw))

    # Title, MARGIN below the top edge.
    add_text(total_w / 2, total_h - MARGIN - title_h / 2, title, 18, "center", bold=True)

    y = total_h - MARGIN - title_h - SECTION_GAP   # top edge of header row
    yc = y - ROW_H / 2
    add_text(x_cat, yc, "Category", HFS, "left", bold=True)
    add_text(x_prog, yc, "Progress", HFS, "left", bold=True)
    add_text(x_val_r, yc, "Actual/Budget", HFS, "right", bold=True)
    add_text(x_diff_r, yc, "Difference", HFS, "right", bold=True)
    seg(x_cat, y - ROW_H + 0.02, total_w - MARGIN, y - ROW_H + 0.02,
        color="black", linewidth=1.0)
    y -= ROW_H

    box_h = ROW_H * 0.62
    for label, its in ordered:
        y -= SECTION_GAP  # breathing room before each section label
        add_text(x_cat, y - ROW_H / 2, label, SFS, "left", bold=True)
        y -= ROW_H
        for it in its:
            yc = y - ROW_H / 2
            by = yc - box_h / 2
            frac = _fraction(it["total"], it["annual"])
            ideal_frac = _fraction(it["ideal"], it["annual"])
            # Red box when over the annual budget, or when absent from the budget.
            red = (frac > 1.0 + 1e-9) or (not it["in_budget"])
            edge = "red" if red else "black"
            fill_w = PROG_W if red else max(0.0, min(frac, 1.0)) * PROG_W

            rect(x_prog, by, PROG_W, box_h, fill=False, edgecolor=edge, linewidth=1.6)
            if fill_w > 0:
                rect(x_prog, by, fill_w, box_h, edgecolor="none", alpha=0.85,
                     facecolor="#c0392b" if red else "#4c72b0")
            if not red and 0 < ideal_frac <= 1.0:
                xt = x_prog + ideal_frac * PROG_W
                seg(xt, by, xt, by + box_h, color="black", linewidth=1.2, linestyle="--")

            color = "red" if red else "black"
            add_text(x_cat, yc, it["name"], FS, "left", color=color)
            add_text(x_val_r, yc, _val_str(it), FS, "right", color=color)
            add_text(x_diff_r, yc, _diff_str(it), FS, "right", color=color)
            y -= ROW_H

    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: python3 check_budge.py <budget_csv>")
    budget_path = argv[1]
    if not os.path.exists(budget_path):
        raise SystemExit(f"Budget file not found: {budget_path}")

    if not lu.ledger_files():
        raise SystemExit("No ledgers/<year>.csv files found.")
    year, path = lu.latest_entry_ledger()
    df = lu.add_usd(lu.read_ledger(path))
    months = ledger_month_span(df)
    budgets = load_budget(budget_path)
    sections = build_items(df, budgets, months)

    # Console report
    print(f"Ledger {year} ({months} months elapsed) vs {os.path.basename(budget_path)}\n")
    print("[categories]  annual budget prorated to date")
    for it in sections["category"]:
        print(f"  {it['name']:<16} total {it['total']:>10,.2f}  "
              f"progress {it['ideal']:>10,.2f}  Δ {it['diff']:>+10,.2f}")
    for label, key in (("projects", "project"), ("tags", "tag")):
        print(f"\n[{label}]  vs full annual budget")
        for it in sections[key]:
            flag = "  <-- not in budget" if not it["in_budget"] else ""
            print(f"  {it['name']:<16} total {it['total']:>10,.2f}  "
                  f"budget {it['annual']:>10,.2f}  Δ {it['diff']:>+10,.2f}{flag}")

    missing = [it["name"] for k in ("project", "tag") for it in sections[k]
               if not it["in_budget"]]
    if missing:
        print("\nFlagged (in ledger, not in budget): " + ", ".join(missing))

    last = df["date"].max()
    out_path = os.path.join(lu.LEDGER_DIR, "budget_progress.png")
    render(sections, out_path, f"Budget Progress {last.year:04d}-{last.month:02d}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
