# Metric dictionary

`maipa_books_metrics {book_id, from, to}` (service `ledger/metrics.py`) returns a fixed list of named figures for a
date range. Each has a name, a plain definition, a unit, its value, the statement line it ties to and the inputs it
was computed from. Money values are the same exact figures as the financial report. Percentages (two decimals) and
weeks (one decimal) are computed from the listed inputs with decimal arithmetic, rounded half up.

| Key | Meaning | Ties to | Not available when |
|---|---|---|---|
| `revenue` | Income earned in the period | Income statement: total income | |
| `expenses` | Costs recorded in the period | Income statement: total expenses | |
| `net_income` | Revenue minus expenses | Income statement: net income | |
| `net_margin` | Net income as a percentage of revenue | Income statement | `no_revenue` |
| `revenue_change` | Revenue against the period of the same length just before | Income statement, both periods | `previous_period_outside_books`, `no_previous_revenue` |
| `cash` | Money in all bank and cash accounts at the end | Cash flow: closing cash | `no_cash_accounts` |
| `usable_cash` | Cash not set aside as restricted | Cash flow, less restricted accounts | `no_cash_accounts` |
| `net_cash_change` | Change in cash during the period | Cash flow: net change | `no_cash_accounts` |
| `cash_runway` | Weeks usable cash lasts at this period's average weekly decrease | Usable cash and its posted movements | `no_cash_accounts`, `cash_not_decreasing`, `no_usable_cash` |
| `total_assets` | What the business owns or is owed at the end | Balance sheet: total assets | |
| `total_liabilities` | What the business owes at the end | Balance sheet: total liabilities | |
| `net_worth` | Total assets minus total liabilities | Balance sheet | |

Nothing is estimated. A metric that cannot be computed has `available: false`, `value: null` and a `reason`; the agent
is told to say so in plain words. Metrics that would need information the books do not hold (for example working
capital, which needs current and long-term balances kept apart) are not in the dictionary.
