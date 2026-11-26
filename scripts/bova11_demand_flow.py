#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 — Demand Flow (mudanças por contrato, v4)
======================================================

Compara D/D-1 nos mesmos contratos, preservando a integração do dashboard.
Normalização transversal descreve mudanças relativas: movimentos uniformes
ficam centrados em zero. Pesos e limiares Bull/Bear são heurísticos, sem
calibração de previsão nem inferência das posições dos dealers.

- descoberta dinâmica por tag/ref-date
- filtro de família do vencimento
- histórico JSON
- HTML auto-contido para o dashboard
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from .bova11_expiry_family import filter_expiry_family
    from .bova11_shared import discover_date_tags, tag_to_iso_nearest, normalize_tag, parse_b3_close_csv
except ImportError:
    from bova11_expiry_family import filter_expiry_family
    from bova11_shared import discover_date_tags, tag_to_iso_nearest, normalize_tag, parse_b3_close_csv


_BASEDIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(_BASEDIR, "..", "history", "bova11_demand_flow.json")

WEIGHTS = {
    "skew": 0.15 / 0.82,
    "premium": 0.20 / 0.82,
    "oi": 0.20 / 0.82,
    "iv": 0.15 / 0.82,
    "gex": 0.12 / 0.82,
}
SCHEMA_VERSION = "demand-change-v4"
MIN_WEIGHT_COVERAGE = 0.50
Z_CAP = 3.0
CONTRACT_KEYS = ["strike", "call_ticker", "put_ticker"]

VENC_DATES = {
    "27 mar w4": "2026-03-27",
    "2 abr w1": "2026-04-02",
    "10 abr w2": "2026-04-10",
    "17 abr mensal": "2026-04-17",
    "24 abr w2": "2026-04-24",
    "30 abr w5": "2026-04-30",
    "15 mai mensal": "2026-05-15",
}

MONTHS_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def parse_num(x) -> float:
    if pd.isna(x):
        return np.nan
    s = str(x).strip().replace("\xa0", "").replace(" ", "")
    mult = 1.0
    if s.endswith(("k", "K")):
        mult = 1e3
        s = s[:-1]
    elif s.endswith(("M", "m")):
        mult = 1e6
        s = s[:-1]
    if s.endswith("%"):
        mult *= 0.01
        s = s[:-1]
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s) * mult
    except (ValueError, OverflowError):
        return np.nan


def robust_z(series: pd.Series) -> pd.Series:
    s = series.astype(float).replace([np.inf, -np.inf], np.nan)
    med = s.median()
    mad = (s - med).abs().median()
    if pd.isna(mad) or mad == 0:
        denom = s.std()
        denom = denom if denom and not pd.isna(denom) else 1.0
        return ((s - med) / denom).clip(-Z_CAP, Z_CAP)
    return (0.6745 * (s - med) / mad).clip(-Z_CAP, Z_CAP)


def classify(score: float) -> str:
    if score is None or not math.isfinite(score):
        return "N/A"
    if score >= 0.25:
        return "Bear"
    if score <= -0.25:
        return "Bull"
    return "Base"


def _safe_json(payload) -> str:
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, (float, np.floating)) and not math.isfinite(value):
            return None
        return value
    return json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")


def _fmt_label(raw: str) -> str:
    meses = {
        "jan": "Jan", "fev": "Fev", "mar": "Mar", "abr": "Abr",
        "mai": "Mai", "jun": "Jun", "jul": "Jul", "ago": "Ago",
        "set": "Set", "out": "Out", "nov": "Nov", "dez": "Dez",
    }
    parts = raw.strip().split()
    out = []
    for part in parts:
        lp = part.lower()
        if lp in meses:
            out.append(meses[lp])
        elif lp.startswith("w") and lp[1:].isdigit():
            out.append("— " + lp.upper())
        elif lp == "mensal":
            out.append("— Mensal")
        else:
            out.append(part.capitalize())
    return " ".join(out)


def _resolve_exp_date(label_raw: str, ref_year: int) -> str:
    exp_date = VENC_DATES.get(label_raw)
    if exp_date:
        return exp_date
    match = re.match(r"(\d{1,2})\s+([a-z]{3})", label_raw)
    if not match:
        return ""
    month = MONTHS_PT.get(match.group(2), 1)
    return f"{ref_year}-{month:02d}-{int(match.group(1)):02d}"


def parse_vencimento_for_sort(venc_str: str) -> tuple:
    match = re.match(r"(\d{1,2})\s+([a-z]{3})", venc_str.lower())
    if not match:
        return (99, 99)
    return (MONTHS_PT.get(match.group(2), 99), int(match.group(1)))


def read_close(path: Path) -> pd.DataFrame:
    rows = parse_b3_close_csv(str(path), merge_volume=False)
    columns = ["call_ticker", "put_ticker", "strike", "call_oi", "put_oi", "call_delta",
               "call_gamma", "call_theta", "call_vega", "call_iv", "call_bid", "call_ask",
               "put_bid", "put_ask", "put_iv", "put_vega", "put_theta", "put_gamma", "put_delta"]
    return pd.DataFrame([{key: row.get(key) for key in columns} for row in rows], columns=columns)


def read_volume(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="latin1", sep=";")
    for col in df.columns:
        if col not in ("Ativo", "Ativo.1"):
            df[col] = df[col].map(parse_num)
    out = pd.DataFrame({
        "call_ticker": df.iloc[:, 0].astype(str).str.strip(),
        "put_ticker": df.iloc[:, 10].astype(str).str.strip() if df.shape[1] > 10 else "",
        "strike": df.iloc[:, 5],
        "call_vol": df.iloc[:, 1],
        "call_oi_volume": df.iloc[:, 2],
        "put_oi_volume": df.iloc[:, 8],
        "put_vol": df.iloc[:, 9],
    })
    return out


def _deduplicate_contracts(df: pd.DataFrame) -> pd.DataFrame:
    base = df[df["strike"] > 0].copy()
    for col in CONTRACT_KEYS[1:]:
        base[col] = base[col].fillna("").astype(str).str.strip().str.upper()
    known = base["call_ticker"].ne("") & base["put_ticker"].ne("")
    return pd.concat([base[known].drop_duplicates(CONTRACT_KEYS),
                      base[~known].drop_duplicates()]).sort_values("strike").reset_index(drop=True)



def infer_spot(df: pd.DataFrame) -> float:
    valid = df.dropna(subset=["call_delta"])
    if valid.empty:
        return float(df["strike"].median())
    idx = (valid["call_delta"] - 0.5).abs().idxmin()
    return float(valid.loc[idx, "strike"])


def build_day(
    close_path: Path,
    vol_path: Optional[Path],
    expiry_label: str,
    session_date: str,
    exp_date: str,
    exp_type: str,
    spot_override: Optional[float] = None,
) -> pd.DataFrame:
    try:
        close_df = read_close(close_path)
    except ValueError as exc:
        print(f"⚠ {expiry_label}: {exc} — pulando vencimento")
        return pd.DataFrame(columns=["_schema_invalid"])
    close_df = filter_expiry_family(close_df, exp_date, exp_type)
    close_df = _deduplicate_contracts(close_df)
    if close_df.empty:
        return close_df

    if vol_path and vol_path.exists():
        vol_df = read_volume(vol_path)
        vol_df = filter_expiry_family(vol_df, exp_date, exp_type)
        vol_df = _deduplicate_contracts(vol_df)
        vol_df = vol_df[vol_df.call_ticker.ne("") & vol_df.put_ticker.ne("")]
    else:
        vol_df = pd.DataFrame(columns=CONTRACT_KEYS + ["call_vol", "put_vol", "call_oi_volume", "put_oi_volume"])

    df = close_df.drop(columns=["call_vol", "put_vol"], errors="ignore").merge(
        vol_df[CONTRACT_KEYS + ["call_vol", "put_vol", "call_oi_volume", "put_oi_volume"]], on=CONTRACT_KEYS, how="left",
        validate="many_to_one",
    )
    df["call_oi"] = df["call_oi"].combine_first(df["call_oi_volume"])
    df["put_oi"] = df["put_oi"].combine_first(df["put_oi_volume"])
    for col in ("call_vol", "put_vol"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    spot = float(spot_override) if spot_override else infer_spot(df)
    df["expiry"] = expiry_label
    df["date"] = session_date
    df["spot_proxy"] = spot
    df["moneyness"] = df["strike"] / max(spot, 1e-9) - 1.0

    for side in ("call", "put"):
        df[f"{side}_mid"] = df[[f"{side}_bid", f"{side}_ask"]].mean(axis=1, skipna=False)
        df[f"{side}_premium_notional"] = df[f"{side}_mid"] * df[f"{side}_vol"]
        sign = 1.0 if side == "call" else -1.0
        # B3 OI is underlying units: monetary delta change for a 1% spot move.
        exposure = sign * spot ** 2 * .01 * df[f"{side}_gamma"] * df[f"{side}_oi"]
        df[f"{side}_net_gex"] = exposure.mask(df[f"{side}_oi"].eq(0), 0.0)
        df[f"{side}_gex_abs"] = df[f"{side}_net_gex"].abs()

    df["net_gex"] = df["call_net_gex"] + df["put_net_gex"]
    return df.sort_values("strike").reset_index(drop=True)


def score_day(df: pd.DataFrame, previous: Optional[pd.DataFrame] = None) -> Dict[str, object]:
    """Descriptive change score on identical contracts; no dealer-position inference."""
    current = _deduplicate_contracts(df)
    if previous is None or previous.empty or "_schema_invalid" in previous:
        previous = current.iloc[:0].copy()
    else:
        previous = _deduplicate_contracts(previous)
    known = lambda frame: frame["call_ticker"].ne("") & frame["put_ticker"].ne("")
    prior = previous.loc[known(previous)]
    pair = current.merge(prior, on=CONTRACT_KEYS, how="left", suffixes=("", "_d1"),
                         indicator=True, validate="many_to_one")
    matched = pair["_merge"].eq("both") & known(pair)
    metrics = pd.DataFrame(index=pair.index)
    iv_region = matched.copy()
    for side in ("call", "put"):
        for suffix in ("", "_d1"):
            iv_region &= pair[f"{side}_delta{suffix}"].abs().between(.1, .9)
    metrics["skew"] = ((pair.put_iv - pair.call_iv) - (pair.put_iv_d1 - pair.call_iv_d1)).where(iv_region)
    metrics["iv"] = ((pair.put_iv + pair.call_iv) - (pair.put_iv_d1 + pair.call_iv_d1)).div(2).where(iv_region)
    for name, field in (("premium", "premium_notional"), ("oi", "oi")):
        metrics[name] = ((pair[f"put_{field}"] - pair[f"call_{field}"]) -
                         (pair[f"put_{field}_d1"] - pair[f"call_{field}_d1"])).where(matched)
    metrics["gex"] = (-pair.net_gex + pair.net_gex_d1).where(matched)
    z = metrics.apply(robust_z)
    # Both sessions' moneyness avoids treating D-1 as if it traded at today's spot.
    distance = ((pair.moneyness.abs() + pair.moneyness_d1.abs()) / 2).fillna(pair.moneyness.abs())
    oi = pair[["call_oi", "put_oi"]].sum(axis=1, min_count=2).clip(lower=0)
    economic = (np.exp(-10.0 * distance) * oi).fillna(0)
    total = economic.sum()
    atm_w = economic / total if total > 0 else economic
    components, coverage_by_component = {}, {}
    for key in WEIGHTS:
        valid_weight = economic.where(z[key].notna(), 0)
        available = valid_weight.sum()
        coverage_by_component[key] = float(available / total) if total > 0 else 0.0
        components[key] = float((z[key].fillna(0) * valid_weight).sum() / available) if available > 0 else None
    active_weights = {key: WEIGHTS[key] * coverage_by_component[key] for key in WEIGHTS}
    coverage = sum(active_weights.values())
    effective = {key: weight / coverage if coverage > 0 else 0.0 for key, weight in active_weights.items()}
    score = sum(effective[key] * value for key, value in components.items() if value is not None) if coverage >= MIN_WEIGHT_COVERAGE else None
    row_coverage = z.notna().mul(pd.Series(WEIGHTS)).sum(axis=1)
    row_score = z.fillna(0).mul(pd.Series(WEIGHTS)).sum(axis=1).div(row_coverage.where(row_coverage > 0))
    row_score = row_score.where(row_coverage >= MIN_WEIGHT_COVERAGE)
    detail = pair[CONTRACT_KEYS + ["moneyness"]].copy()
    detail["atm_weight"], detail["row_score"], detail["coverage"] = atm_w, row_score, row_coverage
    for key in WEIGHTS:
        detail[f"z_{key}"] = z[key]
    dominant = {key: abs(value * effective[key]) for key, value in components.items() if value is not None}
    nullable_sum = lambda values: float(values.sum()) if values.notna().all() else None
    return {
        "schema_version": SCHEMA_VERSION, "score": score, "signal": classify(score),
        "coverage": float(coverage), "component_coverage": coverage_by_component,
        "component_weights": effective, "base_weights": WEIGHTS.copy(), "components": components,
        "dominant_component": max(dominant, key=dominant.get) if dominant else "N/A",
        "spot_proxy": float(current.spot_proxy.iloc[0]),
        "spot_d1": float(previous.spot_proxy.iloc[0]) if not previous.empty else None,
        "ref_date_d1": str(previous.date.iloc[0]) if not previous.empty else None,
        "net_gex": nullable_sum(current.net_gex),
        "gex_abs": nullable_sum(current.call_gex_abs + current.put_gex_abs),
        "total_oi": float(oi.sum()), "economic_total": float(total),
        "aggregation_weight": float(oi.where(row_score.notna(), 0).sum()),
        "available_oi_fraction": float(oi.where(row_score.notna(), 0).sum() / oi.sum()) if oi.sum() > 0 else None,
        "available_expiries": int(score is not None), "total_expiries": 1,
        "matched_contracts": int(matched.sum()), "strikes": int(current.strike.nunique()),
        "rows": json.loads(_safe_json(detail.sort_values("strike").to_dict("records"))),
    }


def discover_expirations_with_volume(data_dir: str, ref_tag: str, ref_year: int) -> List[Dict[str, str]]:
    close_map: Dict[str, Dict[str, str]] = {}
    volume_map: Dict[str, str] = {}
    pattern = re.compile(r"^venc\s+(.+?)\s+fechamento\s+\(([^)]+)\)\.csv$", re.IGNORECASE)

    for name in os.listdir(data_dir):
        if not name.lower().endswith(".csv"):
            continue
        match = pattern.match(name)
        if not match:
            continue
        label_raw = match.group(1).strip().lower()
        token = match.group(2).strip().lower()
        full_path = os.path.join(data_dir, name)
        if token == ref_tag.lower():
            close_map[label_raw] = {
                "label_raw": label_raw,
                "label": _fmt_label(label_raw),
                "exp_date": _resolve_exp_date(label_raw, ref_year),
                "exp_type": "Mensal" if "mensal" in label_raw else "Semanal",
                "close": full_path,
            }
        elif token == f"{ref_tag.lower()} volume":
            volume_map[label_raw] = full_path

    out = []
    for label_raw in close_map.keys() | volume_map.keys():
        item = close_map.get(label_raw) or {
            "label_raw": label_raw, "label": _fmt_label(label_raw),
            "exp_date": _resolve_exp_date(label_raw, ref_year),
            "exp_type": "Mensal" if "mensal" in label_raw else "Semanal", "close": "",
        }
        item["volume"] = volume_map.get(label_raw, "")
        out.append(item)
    out.sort(key=lambda item: parse_vencimento_for_sort(item["label_raw"]))
    return out


def resolve_previous_session(data_dir: str, ref_tag: str, ref_date: str):
    """Use shared discovery; historical runs select the latest strictly earlier date."""
    latest, previous = discover_date_tags(data_dir)
    if latest and normalize_tag(latest) == normalize_tag(ref_tag) and previous:
        prior_date = tag_to_iso_nearest(previous, ref_date)
        if prior_date and prior_date < ref_date:
            return previous, prior_date
    candidates = []
    pattern = re.compile(r"^venc\s+.+?\s+fechamento\s+\(([^)]+)\)\.csv$", re.IGNORECASE)
    for name in os.listdir(data_dir):
        match = pattern.match(name)
        if not match or "volume" in match.group(1).lower():
            continue
        tag = match.group(1).strip().lower()
        session = tag_to_iso_nearest(tag, ref_date)
        if session and session < ref_date:
            priority = 2 if "pos" in tag else 0 if "pre" in tag else 1
            candidates.append((session, os.path.getmtime(os.path.join(data_dir, name)), priority, tag))
    if not candidates:
        return None, None
    session, _, _, tag = max(candidates)
    return tag, session


def load_history() -> Dict[str, Dict[str, object]]:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_history(history: Dict[str, Dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, indent=2)


def upsert_history(history: Dict[str, Dict[str, object]], ref_date: str, expiry: str, result: Dict[str, object]) -> None:
    # Versioned session keys preserve legacy scores and their original meaning.
    key = f"{ref_date}@{SCHEMA_VERSION}"
    entry = {name: value for name, value in result.items() if name != "rows"}
    history.setdefault(key, {})[expiry] = json.loads(_safe_json(entry))


def _missing_expiry_result(label: str, reason: str, spot: float) -> Dict[str, object]:
    """Retain an observed expiry whose closing inventory is unknown."""
    return {
        "schema_version": SCHEMA_VERSION, "expiry_label": label, "missing_data_reason": reason,
        "score": None, "signal": "N/A", "coverage": None,
        "component_coverage": dict.fromkeys(WEIGHTS), "components": dict.fromkeys(WEIGHTS),
        "component_weights": dict.fromkeys(WEIGHTS, 0.0), "base_weights": WEIGHTS.copy(),
        "dominant_component": "N/A", "spot_proxy": spot, "spot_d1": None, "ref_date_d1": None,
        "net_gex": None, "gex_abs": None, "total_oi": None, "economic_total": None,
        "aggregation_weight": 0.0, "available_oi_fraction": None,
        "available_expiries": 0, "total_expiries": 1, "matched_contracts": 0, "strikes": 0, "rows": [],
    }


def _aggregate_results(entries: List[Dict[str, object]]) -> Dict[str, object]:
    usable = [entry for entry in entries if entry["score"] is not None and entry["aggregation_weight"] > 0]
    total = sum(entry["aggregation_weight"] for entry in usable)
    total_oi = sum(entry["total_oi"] for entry in entries) if all(entry.get("total_oi") is not None for entry in entries) else None
    def average(field, key=None):
        values = [(entry[field].get(key) if key else entry[field], entry["aggregation_weight"]) for entry in usable]
        if field == "components":
            values = [(value, weight * entry["component_weights"][key]) for (value, weight), entry in zip(values, usable)]
        values = [(value, weight) for value, weight in values if value is not None]
        denom = sum(weight for _, weight in values)
        return sum(value * weight for value, weight in values) / denom if denom > 0 else None
    def universe_coverage(field, key=None):
        if total_oi is None or total_oi <= 0:
            return None
        return sum((entry[field][key] if key else entry[field]) * entry["total_oi"] for entry in entries) / total_oi
    components = {key: average("components", key) for key in WEIGHTS}
    weights = {key: average("component_weights", key) or 0.0 for key in WEIGHTS}
    dominant = {key: abs(value * weights[key]) for key, value in components.items() if value is not None}
    def summed(field):
        return sum(entry[field] for entry in entries) if all(entry.get(field) is not None for entry in entries) else None
    coverage = universe_coverage("coverage")
    score = average("score") if coverage is not None and coverage >= MIN_WEIGHT_COVERAGE else None
    return {
        "schema_version": SCHEMA_VERSION, "score": score, "signal": classify(score),
        "spot_proxy": average("spot_proxy"), "spot_d1": average("spot_d1"),
        "ref_date_d1": next((entry["ref_date_d1"] for entry in entries if entry["ref_date_d1"]), None),
        "net_gex": summed("net_gex"), "gex_abs": summed("gex_abs"),
        "coverage": coverage,
        "component_coverage": {key: universe_coverage("component_coverage", key) for key in WEIGHTS},
        "component_weights": weights, "base_weights": WEIGHTS.copy(), "components": components,
        "total_oi": total_oi, "economic_total": summed("economic_total"),
        "available_oi_fraction": total / total_oi if total_oi is not None and total_oi > 0 else None,
        "available_expiries": len(usable), "total_expiries": len(entries),
        "universe_complete": total_oi is not None,
        "missing_expiries": [entry.get("expiry_label", "N/A") for entry in entries if entry.get("total_oi") is None],
        "aggregation_weight": total, "strikes": sum(entry["strikes"] for entry in entries),
        "matched_contracts": sum(entry["matched_contracts"] for entry in entries),
        "dominant_component": max(dominant, key=dominant.get) if dominant else "N/A", "rows": [],
    }


def build_payload(results: List[Dict[str, object]], history: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    payload = {item["expiry"]: dict(json.loads(_safe_json(item["result"])), expiry_label=item["expiry"]) for item in results}
    if payload:
        payload["AGG"] = _aggregate_results(list(payload.values()))
    hist_rows = {}
    for session, expiries in sorted(history.items()):
        date_key = session.split("@", 1)[0]
        for expiry, entry in expiries.items():
            # Legacy scores were levels, not changes; do not connect incompatible series.
            if entry.get("schema_version") == SCHEMA_VERSION:
                hist_rows.setdefault(date_key, {})[expiry] = dict(entry, expiry_label=expiry)
    for expiries in hist_rows.values():
        expiries["AGG"] = _aggregate_results([entry for key, entry in expiries.items() if key != "AGG"])
    return {"demand": payload, "history": hist_rows}


def build_html(results: List[Dict[str, object]], history: Dict[str, Dict[str, object]], ref_date: str, ref_tag: str, spot_d: float, spot_d1: float) -> str:
    payload = build_payload(results, history)
    demand_js = _safe_json(payload["demand"])
    history_js = _safe_json(payload["history"])
    exp_keys = [item["expiry"] for item in results]
    exp_keys_js = _safe_json(exp_keys)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    prior_date = next((item["result"].get("ref_date_d1") for item in results if item["result"].get("ref_date_d1")), "N/A")
    missing = [f"{item['expiry']}: {item['result']['missing_data_reason']}" for item in results if item["result"].get("missing_data_reason")]
    universe_warning = (
        '<div class="panel amber" style="padding:14px;margin-bottom:20px">Universo incompleto: '
        + escape("; ".join(missing))
        + '. OI total desconhecido; cobertura e sinal do agregado indisponíveis.</div>'
    ) if missing else ""

    return f"""<!DOCTYPE html>
<html lang="pt-BR" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOVA11 — Demand Flow v4</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="shared.js"></script>
<style>
:root {{
  --bg:#FAFAF8;--bg2:#F0EFEC;--border:#D8D7D4;--t1:#1A1A18;--t2:#4A4A48;--t3:#8A8A88;
  --blu:#0969DA;--red:#CF222E;--grn:#1A7F37;--amber:#B8720A;--card:#FFFFFF;--hdr:#F6F8FA;
}}
[data-theme="dark"] {{
  --bg:#0d1117;--bg2:#161b22;--border:#30363d;--t1:#c9d1d9;--t2:#8b949e;--t3:#6e7681;
  --blu:#58a6ff;--red:#f85149;--grn:#3fb950;--amber:#d29922;--card:#161b22;--hdr:#21262d;
}}
* {{ box-sizing:border-box;margin:0;padding:0; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--t1);font-size:14px; }}
.hdr {{ position:sticky;top:0;z-index:10;background:var(--hdr);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;justify-content:space-between;gap:12px;align-items:center; }}
.hdr-title {{ font-size:1.1rem;font-weight:700; }}
.hdr-sub {{ font-size:.8rem;color:var(--t3);margin-top:2px; }}
.theme-btn {{ background:none;border:1px solid var(--border);border-radius:6px;padding:5px 10px;cursor:pointer;color:var(--t2); }}
.exp-wrap {{ padding:12px 20px 0;background:var(--bg2);border-bottom:1px solid var(--border); }}
.exp-tabs {{ display:flex;flex-wrap:wrap;gap:6px;padding-bottom:12px; }}
.exp-tab {{ padding:5px 12px;border-radius:20px;cursor:pointer;font-size:.8rem;font-weight:500;border:1px solid var(--border);color:var(--t2);background:var(--bg); }}
.exp-tab.on {{ background:var(--blu);border-color:var(--blu);color:#fff; }}
.exp-tab.agg.on {{ background:#8250df;border-color:#8250df; }}
.main {{ padding:20px;max-width:1400px;margin:0 auto; }}
.cards {{ display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px; }}
@media(max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
@media(max-width:520px) {{ .cards {{ grid-template-columns:1fr; }} }}
.card,.panel,.chart-panel {{ background:var(--card);border:1px solid var(--border);border-radius:10px; }}
.card {{ padding:14px 16px; }}
.card-label {{ font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--t3);margin-bottom:6px; }}
.card-val {{ font-size:1.38rem;font-weight:700;font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.card-sub {{ font-size:.75rem;color:var(--t3);margin-top:6px;line-height:1.35; }}
.pos {{ color:var(--red); }} .neg {{ color:var(--grn); }} .neu {{ color:var(--t2); }} .amber {{ color:var(--amber); }}
.two-col {{ display:grid;grid-template-columns:1.05fr .95fr;gap:16px;margin-bottom:20px; }}
@media(max-width:980px) {{ .two-col {{ grid-template-columns:1fr; }} }}
.panel-hdr {{ background:var(--hdr);border-bottom:1px solid var(--border);padding:10px 14px;font-weight:600;font-size:.85rem; }}
.table-wrap {{ max-height:440px;overflow:auto; }}
table {{ width:100%;border-collapse:collapse;font-size:.82rem; }}
th,td {{ padding:7px 10px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ position:sticky;top:0;background:var(--hdr);color:var(--t3);font-size:.72rem;text-transform:uppercase; }}
.chart-panel {{ padding:16px;margin-bottom:20px; }}
.chart-wrap {{ height:250px; }}
.foot {{ margin-top:16px;padding-top:12px;border-top:1px solid var(--border);font-size:.78rem;color:var(--t3);text-align:center; }}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <div class="hdr-title">BOVA11 — Demand Flow v4</div>
    <div class="hdr-sub">{ref_date} | {ref_tag} | D-1: {prior_date} | Spot D: {spot_d:.2f} | Spot D-1: {spot_d1:.2f}</div>
  </div>
  <button class="theme-btn" id="theme-toggle">◐</button>
</div>
<div class="exp-wrap"><div class="exp-tabs" id="expTabs"></div></div>
<main class="main">
  {universe_warning}
  <section class="cards" id="cards"></section>
  <section class="two-col">
    <div class="panel">
      <div class="panel-hdr">Detalhe por strike</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Strike</th><th>Score</th><th>Peso ATM</th><th>Skew</th><th>Premium</th><th>OI</th><th>IV</th><th>GEX</th><th>Cobertura</th></tr>
          </thead>
          <tbody id="detailBody"></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-hdr">Componentes do score</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Componente</th><th>Score</th><th>Peso base</th><th>Peso ativo</th><th>Cobertura</th></tr></thead>
          <tbody id="componentBody"></tbody>
        </table>
      </div>
    </div>
  </section>
  <section class="chart-panel">
    <div class="panel-hdr" style="margin:-16px -16px 14px -16px;border-radius:10px 10px 0 0;">Histórico do score composto</div>
    <div class="chart-wrap"><canvas id="histChart"></canvas></div>
  </section>
  <div class="foot">Variações D/D-1 de contratos idênticos · Skew = Δ(IV put − IV call), |delta| de 0,10 a 0,90 nas duas sessões · IV = ΔIV média · GEX = gamma × OI × spot² × 0,01 · Charm/Vanna indisponíveis · z-score limitado a ±3 · Cobertura mínima 50% · Agregado ponderado pelo OI utilizável; cobertura inclui todos os vencimentos e limita o sinal a ≥50% · Histórico v4 · Gerado em {generated_at}</div>
</main>
<script>
const DEMAND_DATA = {demand_js};
const HISTORY_DATA = {history_js};
const EXP_KEYS = {exp_keys_js};
let curExp = 'AGG';
let histChart = null;

// Override toggleTheme to re-render charts on theme change
var _baseToggle = toggleTheme;
toggleTheme = function() {{
  _baseToggle();
  render();
}}

function tone(v) {{
  if (v > 0.25) return 'pos';
  if (v < -0.25) return 'neg';
  return 'neu';
}}

function fmt(v, d=4) {{
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return 'N/A';
  return Number(v).toLocaleString('pt-BR', {{ minimumFractionDigits:d, maximumFractionDigits:d }});
}}

function pct(v) {{
  return fmt(v === null || v === undefined ? null : v * 100, 1) + '%';
}}

function fmtInt(v) {{
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return 'N/A';
  return Math.round(Number(v)).toLocaleString('pt-BR');
}}

function renderTabs() {{
  const host = document.getElementById('expTabs');
  host.innerHTML = '';
  const agg = document.createElement('button');
  agg.className = 'exp-tab agg' + (curExp === 'AGG' ? ' on' : '');
  agg.textContent = 'AGREGADO';
  agg.onclick = () => {{ curExp = 'AGG'; render(); }};
  host.appendChild(agg);
  EXP_KEYS.forEach(exp => {{
    const btn = document.createElement('button');
    btn.className = 'exp-tab' + (curExp === exp ? ' on' : '');
    btn.textContent = exp;
    btn.onclick = () => {{ curExp = exp; render(); }};
    host.appendChild(btn);
  }});
}}

function renderCards() {{
  const d = DEMAND_DATA[curExp];
  const host = document.getElementById('cards');
  const cls = tone(d.score);
  host.innerHTML = [
    ['Score Composto', `<span class="${{cls}}">${{fmt(d.score, 4)}}</span>`, `${{d.signal}} · dominante: ${{d.dominant_component}} · cobertura do universo: ${{pct(d.coverage)}}${{d.available_oi_fraction !== null && d.available_oi_fraction < 1 ? ' · leitura parcial' : ''}}`],
    ['Spot D', fmt(d.spot_proxy, 2), `D-1: ${{d.ref_date_d1 || 'N/A'}} · spot ${{fmt(d.spot_d1, 2)}} · ${{fmtInt(d.matched_contracts)}} contratos comparáveis`],
    ['Net GEX', `<span class="${{tone(-d.net_gex)}}">${{fmt(d.net_gex, 2)}}</span>`, `GEX absoluto: ${{fmt(d.gex_abs, 2)}}`],
    ['Leitura', `<span class="${{cls}}">${{d.signal}}</span>`, `OI utilizável: ${{pct(d.available_oi_fraction)}} · Vencimentos utilizáveis: ${{fmtInt(d.available_expiries)}}/${{fmtInt(d.total_expiries)}} · cobertura mínima 50%`],
  ].map(([label,val,sub]) => `<div class="card"><div class="card-label">${{label}}</div><div class="card-val">${{val}}</div><div class="card-sub">${{sub}}</div></div>`).join('');
}}

function renderComponents() {{
  const d = DEMAND_DATA[curExp];
  const tbody = document.getElementById('componentBody');
  const rows = Object.entries(d.components || {{}}).sort((a,b) => Math.abs(b[1] || 0) - Math.abs(a[1] || 0));
  tbody.innerHTML = rows.map(([k,v]) => `<tr><td>${{k}}</td><td class="${{tone(v)}}">${{fmt(v,4)}}</td><td>${{fmt(d.base_weights[k],3)}}</td><td>${{fmt(d.component_weights[k],3)}}</td><td>${{pct(d.component_coverage[k])}}</td></tr>`).join('');
}}

function renderDetail() {{
  const d = DEMAND_DATA[curExp];
  const tbody = document.getElementById('detailBody');
  if (!d.rows || !d.rows.length) {{
    tbody.innerHTML = '<tr><td colspan="9">Sem detalhe por strike no agregado.</td></tr>';
    return;
  }}
  tbody.innerHTML = d.rows.map(r => `
    <tr>
      <td><strong>${{fmt(r.strike,0)}}</strong></td>
      <td class="${{tone(r.row_score)}}">${{fmt(r.row_score,3)}}</td>
      <td>${{fmt(r.atm_weight,3)}}</td>
      <td>${{fmt(r.z_skew,3)}}</td>
      <td>${{fmt(r.z_premium,3)}}</td>
      <td>${{fmt(r.z_oi,3)}}</td>
      <td>${{fmt(r.z_iv,3)}}</td>
      <td>${{fmt(r.z_gex,3)}}</td>
      <td>${{fmt(r.coverage * 100,1)}}%</td>
    </tr>`).join('');
}}

function buildHistoryChart() {{
  const ctx = document.getElementById('histChart').getContext('2d');
  const dates = Object.keys(HISTORY_DATA).sort();
  if (histChart) histChart.destroy();
  const expSeries = curExp === 'AGG'
    ? [{{
        label: 'Agregado',
        data: dates.map(dt => HISTORY_DATA[dt] && HISTORY_DATA[dt].AGG ? HISTORY_DATA[dt].AGG.score : null),
        borderColor: '#8250df',
        backgroundColor: 'rgba(130,80,223,.15)',
      }}]
    : [{{
        label: curExp,
        data: dates.map(dt => HISTORY_DATA[dt] && HISTORY_DATA[dt][curExp] ? HISTORY_DATA[dt][curExp].score : null),
        borderColor: '#0969DA',
        backgroundColor: 'rgba(9,105,218,.12)',
      }}];

  histChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: dates, datasets: expSeries.map(s => Object.assign(s, {{ tension:0.25, pointRadius:3, spanGaps:false, fill:true }})) }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: getComputedStyle(document.documentElement).getPropertyValue('--t2') }} }} }},
      scales: {{
        x: {{ ticks: {{ color: getComputedStyle(document.documentElement).getPropertyValue('--t3') }}, grid: {{ color: 'rgba(128,128,128,.10)' }} }},
        y: {{ ticks: {{ color: getComputedStyle(document.documentElement).getPropertyValue('--t3') }}, grid: {{ color: 'rgba(128,128,128,.10)' }} }},
      }},
    }},
  }});
}}

function render() {{
  renderTabs();
  renderCards();
  renderComponents();
  renderDetail();
  buildHistoryChart();
}}

(function() {{
  render();
}})();
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="BOVA11 Demand Flow v3")
    parser.add_argument("--data-dir", required=True, help="Diretório com CSVs B3")
    parser.add_argument("--output", required=True, help="HTML de saída")
    parser.add_argument("--ref-date", required=True, help="Data ISO de referência")
    parser.add_argument("--ref-tag", required=True, help="Tag do CSV (ex: 28abr)")
    parser.add_argument("--spot-d", required=True, type=float, help="Spot D")
    parser.add_argument("--spot-d1", required=True, type=float, help="Spot D-1")
    args = parser.parse_args()
    if not all(math.isfinite(spot) and spot > 0 for spot in (args.spot_d, args.spot_d1)):
        parser.error("--spot-d e --spot-d1 devem ser preços positivos e finitos")

    ref_year = datetime.strptime(args.ref_date, "%Y-%m-%d").year
    expiries = discover_expirations_with_volume(args.data_dir, args.ref_tag, ref_year)
    if not expiries:
        print(f"[ERRO] Nenhum CSV encontrado para tag {args.ref_tag} em {args.data_dir}")
        sys.exit(1)
    tag_d1, date_d1 = resolve_previous_session(args.data_dir, args.ref_tag, args.ref_date)
    prior_expiries = discover_expirations_with_volume(
        args.data_dir, tag_d1, datetime.strptime(date_d1, "%Y-%m-%d").year
    ) if tag_d1 else []
    prior_map = {exp["label_raw"]: exp for exp in prior_expiries}
    if not tag_d1:
        print("  ⚠ Sem sessão D-1 disponível: score será N/A.")

    results: List[Dict[str, object]] = []
    for exp in expiries:
        if not exp.get("close"):
            reason = "Fechamento D ausente"
            results.append({"expiry": exp["label"], "result": _missing_expiry_result(exp["label"], reason, args.spot_d)})
            print(f"  ⚠ {exp['label']}: {reason}; OI do universo desconhecido")
            continue
        day_df = build_day(
            Path(exp["close"]),
            Path(exp["volume"]) if exp.get("volume") else None,
            exp["label"],
            args.ref_date,
            exp["exp_date"],
            exp["exp_type"],
            spot_override=args.spot_d,
        )
        if day_df.empty:
            reason = "Fechamento D inválido" if "_schema_invalid" in day_df.columns else "Fechamento D vazio ou sem dados da família esperada"
            results.append({"expiry": exp["label"], "result": _missing_expiry_result(exp["label"], reason, args.spot_d)})
            print(f"  ⚠ {exp['label']}: {reason}; OI do universo desconhecido")
            continue
        prior = prior_map.get(exp["label_raw"])
        prior_df = build_day(
            Path(prior["close"]), Path(prior["volume"]) if prior.get("volume") else None,
            prior["label"], date_d1, prior["exp_date"], prior["exp_type"],
            spot_override=args.spot_d1,
        ) if prior and prior.get("close") else None
        result = score_day(day_df, prior_df)
        result["ref_tag_d1"] = tag_d1
        results.append({"expiry": exp["label"], "result": result})
        score_label = f"{result['score']:+.4f}" if result["score"] is not None else "N/A"
        print(f"  {exp['label']}: score={score_label} | sinal={result['signal']} | cobertura={result['coverage']:.0%} | spot={result['spot_proxy']:.2f}")

    if not results:
        print("[ERRO] Nenhum vencimento processado.")
        sys.exit(1)

    history = load_history()
    for item in results:
        upsert_history(history, args.ref_date, item["expiry"], item["result"])
    save_history(history)

    html = build_html(results, history, args.ref_date, args.ref_tag, args.spot_d, args.spot_d1)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  HTML gerado: {args.output}")


if __name__ == "__main__":
    main()
