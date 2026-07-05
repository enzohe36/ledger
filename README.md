# Ledger

A small set of Python scripts for a personal double-entry-style ledger.

Each `ledgers/<year>.csv` has the columns:

```
Date,Currency,Account,Category,Project,Tag,Debit,Credit,Description
```

The rows at the top of every file with an empty `Date` are the **category
legend** and are ignored by every script. Amounts are in the entry's `Currency`
(currently `CNY` or `USD`); the budget tools convert everything to **USD** using
daily ECB rates from the [Frankfurter API](https://frankfurter.dev), cached in
`currencies/<CUR>.csv`.

Sign conventions:

* **Balances (assets):** `net = Debit - Credit`. A positive net is shown in the
  `Debit` column, a negative net (money owed / spent) in the `Credit` column.
* **Budget (P&L):** `expense = Credit - Debit`, so **expense is positive and
  income is negative**.

## Requirements

```
pip install pandas matplotlib requests
```

## Scripts

### `check_balances.py`
Summarises the **cumulative** account and project balances through the end of
each year.

```
python3 check_balances.py
```

For every `ledgers/<year>.csv` it writes `ledgers/<year>_balances.csv` with one
line per currency for:

* every account whose balance is non-zero, and
* every project.

Balances accumulate from the lowest year to the highest. An existing balance
file that is byte-identical to the freshly computed one is left untouched; if it
differs, the differences are listed and you are asked before it is overwritten.

### `make_budget.py`
Derives an annualised budget from past activity (all amounts in USD).

```
python3 make_budget.py                # 12 months ending in the last complete month
python3 make_budget.py 202401 202412  # an explicit yyyymm..yyyymm range
```

With no arguments the span is the 12 months ending in the last complete month
(a trailing incomplete month is skipped so the annualisation isn't skewed).

It reports the annualised income/expense per **category** (excluding entries
that carry a project or tag, and the double-entry `Transfer`/`Borrowing`/
`Lending` categories), per **project**, and per **tag**, and lists every
category. Output: `ledgers/budget_<startYYYYMM>_<endYYYYMM>.csv`, where the end
month is the actual last month analysed.

### `check_budget.py`
Compares the highest-year ledger against a budget produced by `make_budget.py`.

```
python3 check_budget.py ledgers/budget_202501_202510.csv
```

* **Categories** — actual total vs the budget *prorated* to the months elapsed
  in the ledger (`annual / 12 * months`, counting the final month even if
  incomplete).
* **Projects / Tags** — actual total vs the full annual budget; anything present
  in the ledger but missing from the budget is flagged.

It writes `ledgers/budget_progress.png`: one progress box per item, using the
annual budget as the divisor. The filled portion is the actual total; a dashed
tick marks the ideal amount. A box that exceeds its annual budget is drawn red
and fully filled. The number beside each box is `ideal - total` (negative means
over the ideal).

### `ledger_utils.py`
Shared helpers used by the scripts above: ledger discovery/loading, the sign
conventions, and the Frankfurter exchange-rate fetch/cache. Not run directly.
