"""ML primitives: train and apply scikit-learn models on catalog sources.

Deliberately a few honest primitives, not AutoML: linear/logistic
regression and random forests, an 80/20 holdout with standard metrics,
models serialized into the content-addressed file store, and predictions
materialized back into datasets so they become catalog sources.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.data_catalog import get_adapter, resolve_source
from genxai.core.files import get_file_store

logger = logging.getLogger(__name__)

MAX_TRAIN_ROWS = 50_000
MODEL_TYPES = {
    "linear_regression",
    "logistic_regression",
    "random_forest_regression",
    "random_forest_classification",
}
_REGRESSORS = {"linear_regression", "random_forest_regression"}


class ModelRegistry:
    """Trained models, metadata in the datasets database, weights in the file store."""

    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    features TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, model_type, source_id, target, features, "
                "metrics, file_id, created_at FROM models ORDER BY created_at DESC"
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get(self, identifier: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, model_type, source_id, target, features, "
                "metrics, file_id, created_at FROM models "
                "WHERE id = ? OR lower(name) = lower(?)",
                (identifier, identifier),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def save(self, model: dict[str, Any]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO models (id, name, model_type, source_id, target, "
                "features, metrics, file_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model["id"],
                    model["name"],
                    model["model_type"],
                    model["source_id"],
                    model["target"],
                    json.dumps(model["features"]),
                    json.dumps(model["metrics"]),
                    model["file_id"],
                    model["created_at"],
                ),
            )

    def delete(self, model_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "name": row[1],
            "model_type": row[2],
            "source_id": row[3],
            "target": row[4],
            "features": json.loads(row[5]),
            "metrics": json.loads(row[6]),
            "file_id": row[7],
            "created_at": row[8],
        }


_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


def reset_model_registry() -> None:
    global _registry
    _registry = None


# ------------------------------------------------------------ train / predict


def _load_frame(
    source_id: str, target: str | None, features: list[str] | None
) -> tuple[list[str], list[list[float]], list[Any], list[dict[str, Any]]]:
    """Rows from a source -> (features, X, y, raw_rows). Numeric features only."""
    source = resolve_source(source_id)
    if source is None:
        raise LookupError(f"Source '{source_id}' not found")
    rows = get_adapter(source).rows(MAX_TRAIN_ROWS, 0)["rows"]
    if not rows:
        raise ValueError(f"Source '{source_id}' has no rows")
    if target is not None and not any(target in row for row in rows[:50]):
        available = sorted({k for row in rows[:20] for k in row if not k.startswith("_")})
        raise ValueError(
            f"Target column '{target}' not found in source '{source_id}' — "
            f"available columns: {', '.join(available)}"
        )

    def numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if not features:
        candidates: list[str] = []
        for row in rows[:200]:
            for key, value in row.items():
                if key.startswith("_") or key == target:
                    continue
                if numeric(value) and key not in candidates:
                    candidates.append(key)
        features = candidates
    if not features:
        raise ValueError("No numeric feature columns found")

    X: list[list[float]] = []
    y: list[Any] = []
    kept: list[dict[str, Any]] = []
    for row in rows:
        values = [row.get(feature) for feature in features]
        if not all(numeric(value) for value in values):
            continue
        if target is not None and (row.get(target) is None or row.get(target) == ""):
            continue
        X.append([float(value) for value in values])
        if target is not None:
            y.append(row.get(target))
        kept.append(row)
    if len(X) < 10 and target is not None:
        raise ValueError(
            f"Only {len(X)} usable rows after dropping incomplete ones — need at least 10"
        )
    return features, X, y, kept


def train_model(
    name: str,
    source_id: str,
    target: str,
    model_type: str,
    features: list[str] | None = None,
) -> dict[str, Any]:
    """Fit, evaluate on an 80/20 holdout, persist, register."""
    import joblib
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.model_selection import train_test_split

    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {sorted(MODEL_TYPES)}")

    feature_names, X, y, _ = _load_frame(source_id, target, features)
    is_regression = model_type in _REGRESSORS
    if is_regression:
        try:
            y = [float(value) for value in y]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Target '{target}' must be numeric for regression"
            ) from exc

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    estimator = {
        "linear_regression": LinearRegression(),
        "logistic_regression": LogisticRegression(max_iter=1000),
        "random_forest_regression": RandomForestRegressor(
            n_estimators=100, random_state=42
        ),
        "random_forest_classification": RandomForestClassifier(
            n_estimators=100, random_state=42
        ),
    }[model_type]
    estimator.fit(X_train, y_train)

    if is_regression:
        from sklearn.metrics import (
            mean_absolute_error,
            mean_squared_error,
            r2_score,
        )

        predictions = estimator.predict(X_test)
        mse = float(mean_squared_error(y_test, predictions))
        metrics = {
            "r2": round(float(r2_score(y_test, predictions)), 4),
            "mae": round(float(mean_absolute_error(y_test, predictions)), 4),
            "mse": round(mse, 4),
            "rmse": round(mse**0.5, 4),
            "train_rows": len(X_train),
            "test_rows": len(X_test),
        }
    else:
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )

        predictions = estimator.predict(X_test)
        metrics = {
            "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
            "precision_weighted": round(
                float(precision_score(
                    y_test, predictions, average="weighted", zero_division=0
                )), 4
            ),
            "recall_weighted": round(
                float(recall_score(
                    y_test, predictions, average="weighted", zero_division=0
                )), 4
            ),
            "f1_weighted": round(
                float(f1_score(y_test, predictions, average="weighted")), 4
            ),
            "train_rows": len(X_train),
            "test_rows": len(X_test),
        }
        # ROC AUC needs class probabilities and >1 class in the holdout;
        # omit rather than fail when either is missing.
        try:
            proba = estimator.predict_proba(X_test)
            classes = list(estimator.classes_)
            if len(classes) == 2:
                positive = classes[1]
                auc = roc_auc_score(
                    [1 if value == positive else 0 for value in y_test],
                    proba[:, 1],
                )
            else:
                auc = roc_auc_score(
                    y_test, proba, multi_class="ovr", average="weighted",
                    labels=classes,
                )
            metrics["roc_auc"] = round(float(auc), 4)
        except Exception:
            pass

    buffer = io.BytesIO()
    joblib.dump({"estimator": estimator, "features": feature_names}, buffer)
    ref = get_file_store().save_bytes(
        buffer.getvalue(), name=f"{name}.joblib", media_type="application/octet-stream"
    )

    model = {
        "id": uuid.uuid4().hex,
        "name": name,
        "model_type": model_type,
        "source_id": source_id,
        "target": target,
        "features": feature_names,
        "metrics": metrics,
        "file_id": ref["id"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    get_model_registry().save(model)
    return model


def predict_with_model(
    identifier: str,
    source_id: str,
    dataset: str | None = None,
    mode: str = "replace",
    limit: int = MAX_TRAIN_ROWS,
) -> dict[str, Any]:
    """Apply a model to a source; optionally materialize predictions."""
    import joblib

    model = get_model_registry().get(identifier)
    if model is None:
        raise LookupError(f"Model '{identifier}' not found")
    bundle = joblib.load(io.BytesIO(get_file_store().read_bytes(model["file_id"])))
    estimator, feature_names = bundle["estimator"], bundle["features"]

    _, X, _, kept = _load_frame(source_id, None, feature_names)
    X, kept = X[:limit], kept[:limit]
    if not X:
        raise ValueError("No rows with all feature columns present")
    predictions = estimator.predict(X)

    column = f"predicted_{model['target']}"
    out_rows = [
        {
            **{k: v for k, v in row.items() if not k.startswith("_")},
            column: (
                prediction.item() if hasattr(prediction, "item") else prediction
            ),
        }
        for row, prediction in zip(kept, predictions)
    ]

    written = None
    if dataset:
        from genxai.core.datasets import get_dataset_store

        store = get_dataset_store()
        if mode == "replace":
            written = store.replace(dataset, out_rows)
        else:
            written = store.append(dataset, out_rows)

    return {
        "model": model["id"],
        "model_name": model["name"],
        "prediction_column": column,
        "rows": out_rows[:50],
        "row_count": len(out_rows),
        "dataset": dataset,
        "written": written,
    }


# --------------------------------------------------------------------- tools


def make_model_train_tool() -> Any:
    from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

    class ModelTrainTool(Tool):
        def __init__(self) -> None:
            super().__init__(
                metadata=ToolMetadata(
                    name="model_train",
                    description=(
                        "Train an ML model (linear/logistic regression or "
                        "random forest) on a data-catalog source and register it"
                    ),
                    category=ToolCategory.AI,
                    tags=["ml", "train", "model", "sklearn"],
                    version="1.0.0",
                ),
                parameters=[
                    ToolParameter(name="name", type="string", description="Model name", required=True),
                    ToolParameter(name="source", type="string", description="Catalog source id", required=True),
                    ToolParameter(name="target", type="string", description="Target column", required=True),
                    ToolParameter(
                        name="model_type",
                        type="string",
                        description="Model family",
                        required=True,
                        enum=sorted(MODEL_TYPES),
                    ),
                    ToolParameter(
                        name="features",
                        type="array",
                        description="Feature columns (default: all numeric)",
                        required=False,
                    ),
                ],
            )

        async def _execute(self, **kwargs: Any) -> dict[str, Any]:
            import asyncio

            return await asyncio.to_thread(
                train_model,
                str(kwargs["name"]),
                str(kwargs["source"]),
                str(kwargs["target"]),
                str(kwargs["model_type"]),
                kwargs.get("features"),
            )

    return ModelTrainTool()


def make_model_predict_tool() -> Any:
    from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

    class ModelPredictTool(Tool):
        def __init__(self) -> None:
            super().__init__(
                metadata=ToolMetadata(
                    name="model_predict",
                    description=(
                        "Apply a registered model to a catalog source; "
                        "optionally write predictions into a dataset"
                    ),
                    category=ToolCategory.AI,
                    tags=["ml", "predict", "model", "sklearn"],
                    version="1.0.0",
                ),
                parameters=[
                    ToolParameter(name="model", type="string", description="Model id or name", required=True),
                    ToolParameter(name="source", type="string", description="Catalog source id", required=True),
                    ToolParameter(
                        name="dataset",
                        type="string",
                        description="Dataset to write predictions into (optional)",
                        required=False,
                    ),
                    ToolParameter(
                        name="mode",
                        type="string",
                        description="replace (default) or append",
                        required=False,
                        default="replace",
                        enum=["replace", "append"],
                    ),
                ],
            )

        async def _execute(self, **kwargs: Any) -> dict[str, Any]:
            import asyncio

            return await asyncio.to_thread(
                predict_with_model,
                str(kwargs["model"]),
                str(kwargs["source"]),
                kwargs.get("dataset") or None,
                str(kwargs.get("mode") or "replace"),
            )

    return ModelPredictTool()


def cross_validate_spec(
    source_id: str,
    target: str,
    model_type: str,
    features: list[str] | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """k-fold cross-validation for a train spec; flags overfitting."""
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.model_selection import cross_validate as sk_cross_validate

    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {sorted(MODEL_TYPES)}")
    feature_names, X, y, _ = _load_frame(source_id, target, features)
    is_regression = model_type in _REGRESSORS
    if is_regression:
        y = [float(v) for v in y]

    estimator = {
        "linear_regression": LinearRegression(),
        "logistic_regression": LogisticRegression(max_iter=1000),
        "random_forest_regression": RandomForestRegressor(
            n_estimators=100, random_state=42
        ),
        "random_forest_classification": RandomForestClassifier(
            n_estimators=100, random_state=42
        ),
    }[model_type]

    k = max(2, min(int(k), 10, len(X) // 2))
    scoring = "r2" if is_regression else "accuracy"
    results = sk_cross_validate(
        estimator, X, y, cv=k, scoring=scoring, return_train_score=True
    )
    val_scores = [round(float(s), 4) for s in results["test_score"]]
    train_mean = float(sum(results["train_score"]) / k)
    val_mean = float(sum(results["test_score"]) / k)
    gap = round(train_mean - val_mean, 4)
    return {
        "metric": scoring,
        "folds": k,
        "scores": val_scores,
        "mean": round(val_mean, 4),
        "std": round(
            (sum((s - val_mean) ** 2 for s in results["test_score"]) / k) ** 0.5, 4
        ),
        "train_mean": round(train_mean, 4),
        "train_val_gap": gap,
        "overfit_warning": gap > 0.15,
        "features": feature_names,
    }


def rank_feature_importance(
    source_id: str,
    target: str,
    model_type: str,
    features: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank features by random-forest importance (the selection workhorse).

    A forest is used for ranking regardless of the final model family —
    it captures non-linear signal and is the standard cheap importance
    estimator. Returns [{feature, importance}] sorted descending.
    """
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {sorted(MODEL_TYPES)}")
    feature_names, X, y, _ = _load_frame(source_id, target, features)
    if model_type in _REGRESSORS:
        y = [float(v) for v in y]
        forest = RandomForestRegressor(n_estimators=100, random_state=42)
    else:
        forest = RandomForestClassifier(n_estimators=100, random_state=42)
    forest.fit(X, y)
    ranked = sorted(
        (
            {"feature": name, "importance": round(float(importance), 4)}
            for name, importance in zip(feature_names, forest.feature_importances_)
        ),
        key=lambda entry: entry["importance"],
        reverse=True,
    )
    return ranked
