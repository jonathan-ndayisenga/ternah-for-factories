# Ternah for Factories — Starter Base

Multi-tenant factory SaaS: platform-owner console, branch-spine data model,
HTMX templates, Inter + Ternah dark-blue navbar, owner charts.

## Run it
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed_superadmin        # superadmin / change-me-now — rotate!
    python manage.py runserver

Login as `superadmin` → /platform/ → **Onboard a Factory** (one screen = the
5-step SaaS onboarding: tenant → admin → Factory Mode modules → tokens → handoff).
A "Factory" branch is auto-created. Then log in as the business admin.

## The spine
Business (tenant) → Branch → Users (manager/cashier/rep/production all carry a
branch FK) → InventoryLocation (identical shape everywhere: factory store,
outlet, rep) → Sales/Expenses/Debtors hang off a location → every owner report
rolls up location → branch → business.

## What's wired
- platformadmin: Business, Module, ModuleSubscription, TokenTransaction,
  AuditLog, soft-lock middleware (423 + locked page, HTMX-aware), top-up =
  instant reactivation, billing_state property (ACTIVE/WARNING/EXPIRED/SOFT_LOCKED)
- accounts: custom User (role, branch, can_swap/can_refund), manager view
  switcher (session `active_view`, banner + Back to Manager)
- production / sales / finance: full models per the SDD (formulas w/ versioning,
  FEFO purchases, distributions, identical inventories, PendingAction engine,
  double-entry journal with balance check)
- reports: owner dashboard — 4 stat cards (HTMX) + 6 Chart.js charts, all
  branch-filterable

## Owner charts (and why)
daily sales 30d (pulse) · revenue vs gross-profit 12m (margin trend, COGS =
production cost) · sales by branch (reps roll up under branch) · top 5 reps ·
payment split (cash vs momo vs credit exposure) · expenses by category

## Next
POS views (reuse Mykashop layout) · production screens · ledger auto-posting
service · cron for the billing lifecycle (warn → grace → lock) · PostgreSQL.
