"""First-run examples for Analytics and Data Science.

Workflows ship as seed JSONs; the data apps need *data*. On startup this
seeds (idempotently, best-effort):

- the ``example_sales`` dataset — a year of deterministic, realistic-ish
  sales rows, so Analytics has something to table/chart/profile/Ask AI
- a federated source ("Example: Revenue by region") showing custom SQL
  over the dataset
- a Data Science analysis ("Example: Sales exploration") with pre-run
  manual cells — no LLM key needed to see real results

Everything is plain demo data generated locally; delete any of it freely.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DATASET = "example_sales"
CHURN_DATASET = "example_churn"
SOURCE_NAME = "Example: Revenue by region"
ANALYSIS_NAME = "Example: Sales exploration"

REGIONS = ("east", "west", "north", "south")
PRODUCTS = ("starter", "pro", "enterprise")
MONTHS = [f"2026-{month:02d}" for month in range(1, 13)]


def _demo_rows() -> list[dict]:
    """Deterministic pseudo-sales: seasonal trend + per-region character."""
    rows = []
    for month_index, month in enumerate(MONTHS):
        seasonal = 1.0 + 0.35 * ((month_index % 6) / 5)  # gentle waves
        for region_index, region in enumerate(REGIONS):
            for product_index, product in enumerate(PRODUCTS):
                base = 40 + 25 * region_index + 60 * product_index
                wiggle = ((month_index * 7 + region_index * 13 + product_index * 5) % 17) - 8
                units = max(3, int((base / 10 + wiggle / 3) * seasonal))
                price = (49, 149, 499)[product_index]
                rows.append(
                    {
                        "month": month,
                        "region": region,
                        "product": product,
                        "units": units,
                        "revenue": round(units * price * seasonal, 2),
                    }
                )
    return rows


def _churn_rows(n: int = 400) -> list[dict]:
    """Deterministic churn data with a learnable signal plus irreducible noise.

    Churn is driven by low tenure, many support tickets, high charges, and
    short contracts — with a noise term that is NOT a feature, so a good
    classifier scores high but not a suspicious 100%.
    """
    rows = []
    for i in range(n):
        tenure_months = 1 + (i * 7) % 60
        support_tickets = (i * 13) % 8
        monthly_charges = 30 + (i * 11) % 90
        contract_months = (0, 12, 24)[(i * 5) % 3]
        usage_gb = 5 + (i * 3) % 80
        plan = "basic" if monthly_charges < 60 else "plus" if monthly_charges < 95 else "premium"
        noise = ((i * 17) % 13) - 6
        score = (
            40
            - tenure_months * 0.9
            + support_tickets * 7
            + monthly_charges * 0.2
            - contract_months * 1.0
            + noise
        )
        rows.append(
            {
                "customer_id": f"C{i:04d}",
                "tenure_months": tenure_months,
                "monthly_charges": monthly_charges,
                "support_tickets": support_tickets,
                "contract_months": contract_months,
                "usage_gb": usage_gb,
                "plan": plan,
                "churned": "yes" if score > 20 else "no",
            }
        )
    return rows


def seed_demo_data() -> None:
    """Idempotent, best-effort: never blocks startup."""
    try:
        _seed_dataset()
        _seed_churn_dataset()
        _seed_federated_source()
        _seed_analysis()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Demo seeding skipped: %s", exc)


def _seed_dataset() -> None:
    from genxai.core.datasets import get_dataset_store

    store = get_dataset_store()
    if store.rows(DATASET, limit=1)["total"] > 0:
        return
    written = store.append(DATASET, _demo_rows())
    logger.info("Seeded dataset '%s' with %d rows", DATASET, written)


def _seed_churn_dataset() -> None:
    """Classification demo: train a churn model on this in Data Science.

    Try: Models rail -> Train (source example_churn, target churned,
    random forest classification), or an Experiment with objective
    "Predict which customers will churn" and target "churned".
    """
    from genxai.core.datasets import get_dataset_store

    store = get_dataset_store()
    if store.rows(CHURN_DATASET, limit=1)["total"] > 0:
        return
    written = store.append(CHURN_DATASET, _churn_rows())
    logger.info("Seeded dataset '%s' with %d rows", CHURN_DATASET, written)


def _seed_federated_source() -> None:
    from app.data_catalog import get_source_registry

    registry = get_source_registry()
    if any(source["name"] == SOURCE_NAME for source in registry.list()):
        return
    registry.create(
        SOURCE_NAME,
        "duckdb",
        {
            "sql": (
                "SELECT region, SUM(revenue) AS revenue, SUM(units) AS units, "
                "ROUND(SUM(revenue) / SUM(units), 2) AS revenue_per_unit "
                "FROM sales GROUP BY region ORDER BY revenue DESC"
            ),
            "sources": {"sales": f"dataset:{DATASET}"},
        },
    )
    logger.info("Seeded federated source '%s'", SOURCE_NAME)


def _seed_analysis() -> None:
    from app.datascience import get_analysis_store, run_manual_cell

    store = get_analysis_store()
    if any(analysis["name"] == ANALYSIS_NAME for analysis in store.list()):
        return
    analysis = store.create(ANALYSIS_NAME, {"sales": f"dataset:{DATASET}"})
    cells = [
        (
            "Which region generates the most revenue?",
            "SELECT region, ROUND(SUM(revenue), 2) AS revenue "
            "FROM sales GROUP BY region ORDER BY revenue DESC",
        ),
        (
            "How does revenue trend month by month?",
            "SELECT month, ROUND(SUM(revenue), 2) AS revenue "
            "FROM sales GROUP BY month ORDER BY month",
        ),
        (
            "Which product line earns the most per unit?",
            "SELECT product, SUM(units) AS units, ROUND(SUM(revenue), 2) AS revenue, "
            "ROUND(SUM(revenue) / SUM(units), 2) AS revenue_per_unit "
            "FROM sales GROUP BY product ORDER BY revenue_per_unit DESC",
        ),
    ]
    for question, sql in cells:
        analysis["cells"].append(run_manual_cell(analysis, sql, question))
    store.save(analysis)
    logger.info(
        "Seeded analysis '%s' with %d cells", ANALYSIS_NAME, len(analysis["cells"])
    )
