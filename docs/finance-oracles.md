# Independent finance acceptance seeds

These synthetic outcomes constrain future ledger tests. They are not implemented
finance capabilities, accounting/tax advice or imported client/corpus data.
Activate each scenario in `tests/acceptance.json` with executable coverage when
its implementation work starts. Do not mark it covered by these prose examples.

| Acceptance seed | Given / action | Independent expected outcome |
|---|---|---|
| L02 exact decimal | Add USD decimal strings `0.10` and `0.20` | `0.30`, never a binary floating-point approximation |
| L02 balanced posting | Debit Cash `100.00`, credit Owner capital `100.00` | Debits and credits each `100.00`; one balanced entry |
| L02 imbalance | Debit Cash `100.00`, credit Owner capital `99.99` | Reject the entire journal; no partial lines or changed balances |
| L03 reversal | Reverse the balanced `100.00` entry | Original stays immutable; linked reversal nets each account to `0.00` |
| L03 period lock | Post or reverse into a locked period | Refuse without an authorized, recorded policy transition |
| L01 idempotency | Submit the same source event twice | Same verified result; exactly one economic event/posting |
| P02 isolation | User A asks for another entity's journal by ID | No data or existence disclosure and no state change |
| A03 authority | Replay an expired or changed-payload approval | No tool execution; new exact authorization is required |

Expected outcomes must come from the relevant invariant/evidence and risk,
not from the implementation's current return values. Add rounding, currency,
reversal lineage, concurrent replay and failure recovery cases when those
boundaries become active. Current high-stakes tax/rule application will require
current primary authority and qualified review; these arithmetic/control seeds
do not establish it.
