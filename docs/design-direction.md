# Approved design: Calm ledger, with dark mode

The user approved ArtBoard `maipa-k02-visual-direction` in AgentInbox message
`msg_555611cc-dee8-4a5e-8066-8945cab33dda` (seq731, 2026-10-24):
**“A but include support for darkmode.”** The duplicate visual-choice question
was withdrawn. This resolves the visual direction for implementation.

Use the same warm finance workspace and interaction in both themes. The Fancy
Ledger/Merchant gallery blueprints are read-only recipes, not vendored code.

| Token | Light | Dark |
|---|---|---|
| Canvas | `#F6F7F5` | `#111714` |
| Surface | `#FFFFFF` | `#18201D` |
| Raised surface | `#FFFFFF` | `#202B25` |
| Primary text | `#1A2420` | `#E8EEE9` |
| Secondary text | `#5C6B64` | `#A7B5AB` |
| Decorative border | `#E4E8E5` | `#34443B` |
| Control border | `#7B8C81` | `#718478` |
| Action/focus | `#4F46E5` | `#A5B4FC` |
| Text on action | `#FFFFFF` | `#171A2B` |
| Positive text | `#047857` | `#6EE7B7` |
| Review text | `#B45309` | `#FCD34D` |
| Error text | `#B91C1C` | `#FCA5A5` |

The deeper light positive-text shade preserves A's emerald direction while
improving small financial text contrast. Semantic states also use text/icons.
Use system sans/mono fonts, tabular figures, 14px body text, 14px card radius,
8px control radius, restrained shadows and a small brand mark.

Theme preference offers **System / Light / Dark**, defaults to the operating
system and follows system changes only while System is selected. Set the root
theme before rendering settled content. Preserve an explicit user's choice
across sessions under that authenticated user's preference scope; applying a
theme never changes records, permissions, conversations or the active view.
Native controls, menus, charts, tables, skeletons, tooltips, focus and error/
loading/disabled states must all use the selected theme. Respect reduced motion.
Verify contrast and real keyboard/focus behavior in both themes; a palette or
static markup check does not establish browser accessibility acceptance.

Keep persistent chat/Agent and dynamic User Panels mounted in the same shell,
without page navigation. Wide initial split is 28/72; use published Fancy
ResizablePanels controlled percentages and persist onValueCommit per user.
Support stacked panes and narrow-screen visibility switching while preserving
both contexts. Named divider, arrow/Shift-arrow/Home/End controls, focus return
and hidden-pane accessibility behavior remain required.

The User Panel owns authorized entity/book/period context, registered views,
owned custom-view pins and authorized admin/preferences. Use published Fancy
primitives, fancy-screens and agent-integrations; never execute arbitrary
model-generated view code. Agent Settings reflects actual supported ACP
account/provider/runtime capability, not a disconnected selector. Exact
proposal, approval, execution, verification and failure states stay distinct.

Use the existing loading/empty/error/keyboard/streaming acceptance in the
envelope's K02 review, now extended to both themes. No synthetic values are
presented as connected financial data. Theme changes may be immediate cosmetic
operations; finance/provider operations retain backend authority checks.

K02 is design acceptance. Implementation, app persistence, package integration,
both-theme browser verification and the exact-candidate release gate remain
work for the corresponding implementation tasks.
