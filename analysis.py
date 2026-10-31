"""Análisis educativo de EverPeak. No modifica el archivo de origen."""
import json
from pathlib import Path

import numpy as np
import pandas as pd


NUMERIC = ["price", "quantity", "order_value", "customer_age"]
ANALYSIS_YEAR = 2024


def prepare_data(raw, year=ANALYSIS_YEAR):
    """Normaliza tipos, conserva indicadores y delimita el periodo analizado."""
    df = raw.copy(deep=True)
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["product_category", "payment_method", "city", "state"]:
        df[col] = df[col].astype("string").str.strip()
        df[col] = df[col].replace({"?": pd.NA, "": pd.NA}).fillna("unknown")
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    date_missing = df["order_date"].isna()
    date_outside = df["order_date"].notna() & df["order_date"].dt.year.ne(year)
    date_audit = {
        "missing_date": int(date_missing.sum()),
        "outside_period": int(date_outside.sum()),
    }
    df = df.loc[~date_missing & ~date_outside].copy()

    # Solo reconstruir cantidades no positivas cuando total / precio es entero.
    # Esto supone total = precio * cantidad, sin descuentos ni cargos extra.
    invalid_qty = df["quantity"].isna() | df["quantity"].le(0)
    ratio = df["order_value"] / df["price"].where(df["price"].gt(0))
    coherent = (
        invalid_qty & ratio.notna() & ratio.ge(1)
        & np.isclose(ratio, ratio.round(), atol=1e-9, rtol=0)
    )
    df["quantity_invalid_flag"] = invalid_qty.astype(int)
    df["quantity_reconstructed_flag"] = coherent.astype(int)
    df.loc[invalid_qty, "quantity"] = np.nan
    df.loc[coherent, "quantity"] = ratio.loc[coherent].round()

    invalid_age = (
        df["customer_age"].isna()
        | df["customer_age"].isin([-999, 999, 0, -1])
        | df["customer_age"].lt(0) | df["customer_age"].gt(120)
    )
    df["age_imputed_flag"] = invalid_age.astype(int)
    df.loc[invalid_age, "customer_age"] = np.nan
    age_median = df["customer_age"].median()
    df["customer_age"] = df["customer_age"].fillna(age_median)

    audit = {
        "input_rows": int(len(raw)),
        "analysis_rows": int(len(df)),
        "year": int(year),
        "exact_duplicates_original": int(raw.duplicated().sum()),
        **date_audit,
        "quantity_reconstructed": int(coherent.sum()),
        "quantity_unresolved": int(df["quantity"].isna().sum()),
        "quantity_total_disagreements": int((df["quantity"].notna() & ~np.isclose(
            df["price"] * df["quantity"], df["order_value"], atol=0.01, rtol=0
        )).sum()),
        "age_imputed": int(invalid_age.sum()),
        "age_median_used": float(age_median),
        "unknown_categories": int(df["product_category"].eq("unknown").sum()),
        "unknown_city": int(df["city"].eq("unknown").sum()),
        "unknown_state": int(df["state"].eq("unknown").sum()),
    }
    assert audit["input_rows"] == len(df) + date_audit["missing_date"] + date_audit["outside_period"]
    assert df["quantity"].dropna().gt(0).all()
    assert df["order_date"].dt.year.eq(year).all()
    return df, audit


def classify_segment(row):
    """Clasifica órdenes; no confunde una orden con un cliente único."""
    age, spend = row["customer_age"], row["order_value"]
    if pd.isna(age) or pd.isna(spend) or row["age_imputed_flag"]:
        return "Datos por validar"
    if spend >= 10000:
        return "Senior VIP" if age >= 55 else "Junior VIP"
    if spend >= 5000:
        return "Sr. Medium Value" if age >= 55 else "Jr. Medium Value"
    return "Low Value"


def describe_numeric(series):
    values = series.dropna()
    q1, q3 = values.quantile([0.25, 0.75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    flags = values.lt(lower) | values.gt(upper)
    standard_deviation = values.std()
    z = (values - values.mean()) / standard_deviation if standard_deviation > 0 else values * 0
    histogram_counts, histogram_edges = np.histogram(values, bins=30)
    inside = values.loc[~flags]
    p99 = values.quantile(0.99)
    capped = values.clip(upper=p99)
    return {
        "stats": {k: float(v) for k, v in values.describe().to_dict().items()},
        "iqr": float(iqr), "lower_fence": float(lower), "upper_fence": float(upper),
        "outliers_iqr": int(flags.sum()), "outliers_z3": int(z.abs().gt(3).sum()),
        "histogram_counts": histogram_counts.tolist(),
        "histogram_edges": histogram_edges.tolist(),
        "box": {"med": float(values.median()), "q1": float(q1), "q3": float(q3),
                "whislo": float(inside.min()), "whishi": float(inside.max())},
        "p99": float(p99), "capped_count": int(values.gt(p99).sum()),
        "mean_capped": float(capped.mean()), "std_capped": float(capped.std()),
        "sum_original": float(values.sum()), "sum_capped": float(capped.sum()),
    }


def analyze(raw):
    df, audit = prepare_data(raw)
    # Los valores monetarios altos se conservan. El capado es solo sensibilidad.
    df["customer_segment"] = df.apply(classify_segment, axis=1)
    result = {
        "analysis_name": "EverPeak 2024: calidad, distribuciones y segmentación",
        "audit": audit,
        "numeric": {col: describe_numeric(df[col]) for col in NUMERIC},
        "segments": {str(k): int(v) for k, v in df["customer_segment"].value_counts().items()},
        "segment_rules": "Gasto >=10000: VIP; 5000<=gasto<10000: medio; gasto<5000: bajo. Senior: edad>=55. Edad imputada: por validar.",
        "quantity_caveat": "Cantidad provisional: la reconstrucción supone total=precio*cantidad. Persisten diferencias aritméticas en cantidades positivas originales. No usar para decisiones de inventario sin validar descuentos, unidades y cargos.",
        "payment_methods": {str(k): int(v) for k, v in df["payment_method"].value_counts().items()},
        "product_categories": {str(k): int(v) for k, v in df["product_category"].value_counts().items()},
        "source": "TripleTen, Sprint 7, /datasets/everpeak_retail.csv",
        "unit_of_analysis": "Orden individual; los conteos no equivalen a clientes únicos.",
        "amount_unit": "Unidades monetarias del dataset; sin inferir una moneda.",
        "date_extracted": "2026-10-31",
    }
    assert sum(result["segments"].values()) == len(df)
    for col in NUMERIC:
        assert sum(result["numeric"][col]["histogram_counts"]) == int(df[col].count())
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="CSV de EverPeak obtenido legítimamente del curso")
    parser.add_argument("--output", type=Path, default=Path("results_aggregate.json"))
    args = parser.parse_args()
    result = analyze(pd.read_csv(args.data))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result["audit"], ensure_ascii=False, indent=2))
