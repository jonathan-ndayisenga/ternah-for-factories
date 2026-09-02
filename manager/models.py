from django.db import models

# Views-only app — no models of its own. It aggregates Product (production),
# Sale/Debtor/Expense/PendingAction (sales), and (later) the ledger (finance)
# behind a branch-locked lens, the same way `reports` does for the owner.
