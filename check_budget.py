#!/usr/bin/env python3
"""check_budget.py -- compare the current ledger against a budget.

Reads the highest-year ledgers/<year>.csv and a budget_*.csv produced by
make_budget.py, then reports (all amounts converted to USD):

  Categories  total income/expense per category (excluding entries with a
              project or tag, and the double-entry categories), the prorated
              budget progress (annual / 12 * months elapsed in the ledger,
              counting the final month even if incomplete), and the gap.
  Projects    total per project vs the prorated budget; projects present in
              the ledger but absent from the budget are flagged.
  Tags        same treatment as projects.

Income and expense are both reported as positive Actuals (income = Debit -
Credit, expense = Credit - Debit).  Balance is Actual - Budget for income and
Budget - Actual for expense.

It renders budget_progress.png with columns Category, Progress, YTD Actual,
Prorated (budget-to-date), Annual (annual budget) and Balance.  Each progress box uses
the annual budget as the divisor: the filled portion is the Actual (red when
the Balance is negative -- Actual past the budget-to-date for expense, short of
it for income -- otherwise blue) and a dashed tick marks where the
budget-to-date sits.  The Income, Category Expense, Project Expense
and Tagged Expense sections each close with a plain "Subtotal" row; a bold
"Total" row closes the report -- Income minus every expense section, with its
Balance recomputed as the net Actual minus the net Budget.

Usage:  python3 check_budget.py <budget_csv>
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
    df["net_usd"] = df["credit_usd"] - df["debit_usd"]  # money out +, money in -

    sections = {}

    # Categories: exclude project/tag entries and double-entry categories.
    cat_mask = ((df["Project"] == "") & (df["Tag"] == "") &
                (~df["Category"].isin(lu.DOUBLE_ENTRY_CATEGORIES)))
    cat_net = df[cat_mask].groupby("Category")["net_usd"].sum().to_dict()
    sections["category"] = _items(budgets["category"], cat_net,
                                   ideal_scale=months / 12.0)

    # Projects & tags: treated as expense, prorated to date like categories.
    proj_net = df[df["Project"] != ""].groupby("Project")["net_usd"].sum().to_dict()
    sections["project"] = _items(budgets["project"], proj_net,
                                 ideal_scale=months / 12.0)

    tag_net = df[df["Tag"] != ""].groupby("Tag")["net_usd"].sum().to_dict()
    sections["tag"] = _items(budgets["tag"], tag_net, ideal_scale=months / 12.0)

    return sections


def _items(budget_map, nets, ideal_scale):
    """Build item dicts.  Both income (Debit - Credit) and expense
    (Credit - Debit) are stored as positive Actuals; Balance is Actual - Budget
    for income and Budget - Actual for expense."""
    names = sorted(set(budget_map) | set(nets))
    items = []
    for name in names:
        income = name in lu.INCOME_CATEGORIES
        annual = budget_map.get(name, 0.0)             # positive for both
        net = nets.get(name, 0.0)                       # credit - debit
        total = -net if income else net                 # Actual, positive
        ideal = annual * ideal_scale                    # Budget-to-date
        diff = (total - ideal) if income else (ideal - total)  # Balance
        items.append({
            "name": name, "annual": annual, "total": total,
            "ideal": ideal, "diff": diff, "income": income,
            "in_budget": name in budget_map,
        })
    return items


def _fraction(total, annual):
    if annual != 0:
        return total / annual
    return 1.0 if abs(total) > 1e-9 else 0.0


def _is_income(it):
    return it["income"]


# Fixed layout constants (inches). Text columns are sized to their content;
# GAP is the constant spacing between every column.
GAP = 0.30         # spacing between columns
PROG_W = 3.20      # progress-bar column width
ROW_H = 0.34       # height of one row
SECTION_GAP = 0.20  # extra vertical space before the header and each section label
MARGIN = SECTION_GAP     # figure margins
FS = HFS = SFS = 14  # item / header / section-label font sizes


def _is_red(it):
    # Red marks an unfavourable position: a negative Balance (Actual past the
    # budget-to-date for an expense, short of it for income).
    return it["diff"] < -1e-9


def _exp_str(it):
    return f"{it['total']:,.0f}"


def _bud_str(it):
    # Budget-to-date: prorated annual for categories, full annual for
    # projects/tags (where ideal == annual).
    return f"{it['ideal']:,.0f}"


def _tot_str(it):
    return f"{it['annual']:,.0f}"


def _diff_str(it):
    # Difference == Budget - Expense == ideal - total.
    return f"{it['diff']:+,.0f}"


def _sum_row(name, group):
    """A synthetic aggregate row: every numeric field summed over ``group``."""
    return {"name": name,
            "total": sum(it["total"] for it in group),
            "ideal": sum(it["ideal"] for it in group),
            "annual": sum(it["annual"] for it in group),
            "diff": sum(it["diff"] for it in group)}


def _net_total(name, income_sub, expense_subs):
    """Income subtotal minus all expense subtotals, column by column.  The
    Balance is recomputed as the net Actual minus the net Budget."""
    def net(f):
        inc = income_sub[f] if income_sub else 0.0
        return inc - sum(s[f] for s in expense_subs)
    total, ideal = net("total"), net("ideal")
    return {"name": name, "total": total, "ideal": ideal,
            "annual": net("annual"), "diff": total - ideal}


def render(sections, out_path, title):
    cats = sections["category"]
    income = [i for i in cats if _is_income(i)]
    expense = [i for i in cats if not _is_income(i)]
    ordered = [("Income", income, "income"),
               ("Category Expense", expense, "expense"),
               ("Project Expense", sections["project"], "project"),
               ("Tagged Expense", sections["tag"], "tag")]
    ordered = [(lbl, its, kind) for lbl, its, kind in ordered if its]
    items = [it for _, its, _ in ordered for it in its]

    # A linear element list drives both height and drawing.  "gap" carries a
    # height; "divider" carries a linewidth (pt) and draws at the row boundary;
    # every text row is ROW_H tall.
    subtotals = {}
    elems = [("gap", SECTION_GAP), ("header",), ("divider", 2.0)]
    for label, its, kind in ordered:
        sub = _sum_row("Subtotal", its)
        subtotals[kind] = sub
        elems.append(("gap", SECTION_GAP))     # space above each section header
        elems.append(("section", label))
        elems.extend(("item", it) for it in its)
        elems += [("divider", 1.0), ("subtotal", sub)]

    # Grand total: Income minus every expense section, column by column.
    grand = _net_total("Total", subtotals.get("income"),
                       [subtotals[k] for k in ("expense", "project", "tag")
                        if k in subtotals])
    elems += [("gap", SECTION_GAP), ("total", grand)]

    sum_rows = [e[1] for e in elems if e[0] in ("subtotal", "total")]

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

    # Category is content-sized.  Actual/Budget/Annual B/Balance share one fixed
    # width (the widest of the four, headers included) so their columns line up.
    w_cat = max(text_w("Category", HFS, True),
                col_w([lbl for lbl, _, _ in ordered], SFS, True),
                col_w([it["name"] for it in items], FS, False),
                col_w([r["name"] for r in sum_rows], FS, True))
    num_strings = [f(r) for f in (_exp_str, _bud_str, _diff_str, _tot_str)
                   for r in items + sum_rows]
    w_num = max(text_w("YTD Actual", HFS, True), text_w("Prorated", HFS, True),
                text_w("Annual", HFS, True), text_w("Balance", HFS, True),
                col_w(num_strings, FS, True))

    # Column anchors.  Item/Progress are left aligned (x is the left edge);
    # Actual/Budget/Annual B/Balance are right aligned (x is the right edge).
    x_cat = MARGIN
    x_prog = x_cat + w_cat + GAP
    x_exp_r = x_prog + PROG_W + GAP + w_num
    x_bud_r = x_exp_r + GAP + w_num
    x_tot_r = x_bud_r + GAP + w_num
    x_diff_r = x_tot_r + GAP + w_num
    total_w = x_diff_r + MARGIN

    title_h = 0.30  # height reserved for the title text
    content_h = sum(e[1] if e[0] == "gap" else (0.0 if e[0] == "divider" else ROW_H)
                    for e in elems)
    total_h = MARGIN + title_h + content_h + MARGIN
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

    box_h = ROW_H * 0.62

    def draw_item(it, y):
        yc = y - ROW_H / 2
        by = yc - box_h / 2
        frac = _fraction(it["total"], it["annual"])
        ideal_frac = _fraction(it["ideal"], it["annual"])
        red = _is_red(it)
        fill_w = max(0.0, min(frac, 1.0)) * PROG_W

        # Always a black border; fill red when the Balance is negative, else blue.
        rect(x_prog, by, PROG_W, box_h, fill=False, edgecolor="black", linewidth=1.6)
        if fill_w > 0:
            rect(x_prog, by, fill_w, box_h, edgecolor="none", alpha=0.85,
                 facecolor="#c0392b" if red else "#4c72b0")
        # Always mark where the budget-to-date sits relative to the total budget.
        if 0 < ideal_frac <= 1.0 + 1e-9:
            xt = x_prog + min(ideal_frac, 1.0) * PROG_W
            seg(xt, by, xt, by + box_h, color="black", linewidth=1.2, linestyle="--")

        # Text is always black; only the progress bar reflects the balance.
        add_text(x_cat, yc, it["name"], FS, "left")
        add_text(x_exp_r, yc, _exp_str(it), FS, "right")
        add_text(x_bud_r, yc, _bud_str(it), FS, "right")
        add_text(x_diff_r, yc, _diff_str(it), FS, "right")
        add_text(x_tot_r, yc, _tot_str(it), FS, "right")

    def draw_sum_row(r, y, bold):
        # Aggregate row: no progress bar.
        yc = y - ROW_H / 2
        add_text(x_cat, yc, r["name"], FS, "left", bold=bold)
        add_text(x_exp_r, yc, _exp_str(r), FS, "right", bold=bold)
        add_text(x_bud_r, yc, _bud_str(r), FS, "right", bold=bold)
        add_text(x_diff_r, yc, _diff_str(r), FS, "right", bold=bold)
        add_text(x_tot_r, yc, _tot_str(r), FS, "right", bold=bold)

    y = total_h - MARGIN - title_h          # top of the content area
    for elem in elems:
        kind = elem[0]
        if kind == "gap":
            y -= elem[1]
        elif kind == "divider":
            seg(x_cat, y, total_w - MARGIN, y, color="black", linewidth=elem[1])
        elif kind == "header":
            yc = y - ROW_H / 2
            add_text(x_cat, yc, "Category", HFS, "left", bold=True)
            add_text(x_prog, yc, "Progress", HFS, "left", bold=True)
            add_text(x_exp_r, yc, "YTD Actual", HFS, "right", bold=True)
            add_text(x_bud_r, yc, "Prorated", HFS, "right", bold=True)
            add_text(x_tot_r, yc, "Annual", HFS, "right", bold=True)
            add_text(x_diff_r, yc, "Balance", HFS, "right", bold=True)
            y -= ROW_H
        elif kind == "section":
            add_text(x_cat, y - ROW_H / 2, elem[1], SFS, "left", bold=True)
            y -= ROW_H
        elif kind == "subtotal":
            draw_sum_row(elem[1], y, bold=False)
            y -= ROW_H
        elif kind == "total":
            draw_sum_row(elem[1], y, bold=True)
            y -= ROW_H
        else:  # item
            draw_item(elem[1], y)
            y -= ROW_H

    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: python3 check_budget.py <budget_csv>")
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
