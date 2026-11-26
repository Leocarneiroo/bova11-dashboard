"""
Helpers para limpar CSVs B3 contaminados com múltiplas maturidades.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional, Tuple

MONTH_CODE_BY_MONTH = {
    1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F",
    7: "G", 8: "H", 9: "I", 10: "J", 11: "K", 12: "L",
}

SUM_COLUMNS = {
    "call_oi", "put_oi",
    "call_vol", "put_vol",
    "call_trades", "put_trades",
    "call_oi_v", "put_oi_v",
}

MEAN_COLUMNS = {
    "call_last", "put_last",
    "call_bid", "call_ask", "put_bid", "put_ask",
    "call_delta", "put_delta",
    "call_gamma", "put_gamma",
    "call_theta", "put_theta",
    "call_vega", "put_vega",
    "call_iv", "put_iv",
}

TICKER_FAMILY_RE = re.compile(
    r"^[A-Z]+(?P<month>[A-Z])\d+(?P<suffix>W[1-5])?$",
    re.IGNORECASE,
)


def expected_ticker_family(
    exp_date: str, exp_type: str, option_side: str = "call"
) -> Tuple[str, str]:
    """Retorna código de mês B3 e sufixo semanal esperado."""
    if not exp_date:
        return "", ""
    try:
        exp = datetime.strptime(exp_date, "%Y-%m-%d").date()
    except ValueError:
        return "", ""

    month_code = MONTH_CODE_BY_MONTH.get(exp.month, "")
    if not month_code:
        return "", ""
    if option_side.lower() == "put":
        month_code = chr(ord(month_code) + 12)

    if exp_type and exp_type.lower().startswith("semanal"):
        return month_code, f"W{math.ceil(exp.day / 7)}"
    return month_code, "MENSAL"


def expiry_from_label(label: str) -> Tuple[str, str]:
    """Deriva data-base e tipo do rótulo; semana vem do dia real do vencimento."""
    match = re.search(r"(\d{1,2})\s+([a-z]{3})", str(label).lower())
    if not match:
        return "", ""
    month = {
        "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
        "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    }.get(match.group(2))
    day = int(match.group(1))
    if month is None:
        return "", ""
    try:
        exp_date = datetime(2000, month, day).date().isoformat()
    except ValueError:
        return "", ""
    exp_type = "Semanal" if re.search(r"\bW[1-5]\b", label, re.IGNORECASE) else "Mensal"
    return exp_date, exp_type


def parse_ticker_family(ticker: str) -> Optional[Tuple[str, str]]:
    """Extrai (letra_do_mês, sufixo) de um ticker B3."""
    if not isinstance(ticker, str):
        return None
    match = TICKER_FAMILY_RE.match(ticker.strip().upper())
    if not match:
        return None
    return match.group("month"), (match.group("suffix") or "MENSAL").upper()


def matches_expiry_family(
    ticker: str, exp_date: str, exp_type: str, option_side: str = "call"
) -> bool:
    """Confere se o ticker pertence à família esperada para o vencimento."""
    expected = expected_ticker_family(exp_date, exp_type, option_side)
    if not expected[0]:
        return True
    parsed = parse_ticker_family(ticker)
    if parsed is None:
        return False
    return parsed == expected


def matches_expiry_row(
    call_ticker: str, put_ticker: str, exp_date: str, exp_type: str
) -> bool:
    """Retém a linha apenas se cada ticker presente pertencer ao vencimento."""
    return all(
        not str(ticker or "").strip()
        or matches_expiry_family(ticker, exp_date, exp_type, side)
        for ticker, side in ((call_ticker, "call"), (put_ticker, "put"))
    )


def matches_expiry_label(call_ticker: str, put_ticker: str, label: str) -> bool:
    exp_date, exp_type = expiry_from_label(label)
    return not exp_date or matches_expiry_row(call_ticker, put_ticker, exp_date, exp_type)


def filter_expiry_family(
    df: pd.DataFrame,
    exp_date: str,
    exp_type: str,
    call_col: str = "call_ticker",
    put_col: str = "put_ticker",
) -> pd.DataFrame:
    """Mantém apenas linhas da família correta do vencimento."""
    import pandas as pd

    if df.empty:
        return df.copy()

    expected = expected_ticker_family(exp_date, exp_type)
    if not expected[0]:
        return df.copy()

    ticker_cols = [col for col in (call_col, put_col) if col in df.columns]
    if not ticker_cols:
        return df.copy()

    mask = pd.Series(True, index=df.index)
    for col in ticker_cols:
        side = "put" if col == put_col else "call"
        values = df[col].fillna("").astype(str).str.strip()
        mask &= values.eq("") | values.map(
            lambda ticker: matches_expiry_family(ticker, exp_date, exp_type, side)
        )
    return df.loc[mask].copy()


def finite_number(value):
    """Preserva valor desconhecido; aceita apenas números finitos."""
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def deduplicate_option_records(rows, ticker_keys=("call_ticker", "put_ticker")):
    """Dedup por identidade conhecida; sem tickers só duplicatas idênticas."""
    seen, unique = set(), []
    for row in rows:
        tickers = tuple(str(row.get(key) or "").strip().upper() for key in ticker_keys)
        identity = (row.get("strike"), *tickers) if all(tickers) else tuple(sorted((key, repr(value)) for key, value in row.items()))
        if identity not in seen:
            seen.add(identity)
            unique.append(row)
    return unique


def deduplicate_option_rows(df):
    import pandas as pd
    return pd.DataFrame(deduplicate_option_records(df.to_dict("records")), columns=df.columns)


def weighted_atm_iv(rows, oi_keys=("call_oi", "put_oi"), iv_keys=("call_iv", "put_iv")):
    """IV ponderada por OI observado; sem OI exige IV válida em ambos lados."""
    pairs = [(finite_number(row.get(oi)), finite_number(row.get(iv)))
             for row in rows for oi, iv in zip(oi_keys, iv_keys)]
    if not pairs:
        return None
    oi_known = all(oi is not None and oi >= 0 for oi, _ in pairs)
    total = sum(oi for oi, _ in pairs) if oi_known else 0
    active = [(oi, iv) for oi, iv in pairs if oi and oi > 0] if total else pairs
    if any(iv is None or iv < 0 for _, iv in active):
        return None
    return sum(oi * iv for oi, iv in active) / total if total else sum(iv for _, iv in active) / len(active)


def raw_exposure_records(rows, spot, include_delta=False):
    """Exposição por strike: contribuição ausente invalida soma completa."""
    rows = deduplicate_option_records(rows)
    grouped, metadata = {}, {}
    metrics = [("gamma", "gex", spot ** 2 * .01)] + ([("delta", "dex", spot)] if include_delta else [])
    for greek, metric, factor in metrics:
        observed = covered = partial = 0.0
        oi_known = True
        for row in rows:
            result = grouped.setdefault(row["strike"], {"strike":row["strike"]})
            for side in ("call", "put"):
                oi, value = finite_number(row.get(f"{side}_oi")), finite_number(row.get(f"{side}_{greek}"))
                valid_oi = oi is not None and oi >= 0
                oi_known &= valid_oi
                observed += oi if valid_oi else 0
                valid = valid_oi and (oi == 0 or (value is not None and (greek != "gamma" or value >= 0)))
                covered += oi if valid else 0
                exposure = (0.0 if oi == 0 else value * oi * factor * (-1 if side == "put" and metric == "gex" else 1)) if valid else None
                key = f"{side}_{metric}"
                previous = result.get(key, 0.0)
                result[key] = previous + exposure if previous is not None and exposure is not None else None
                partial += exposure if exposure is not None else 0
        for result in grouped.values():
            call, put = result[f"call_{metric}"], result[f"put_{metric}"]
            result[f"net_{metric}"] = call + put if call is not None and put is not None else None
        metadata[f"{greek}_coverage"] = covered / observed if oi_known and observed else (1.0 if oi_known and rows else 0.0)
        metadata[f"partial_net_{metric}"] = partial
    return [grouped[strike] for strike in sorted(grouped)], metadata


def raw_exposures_by_strike(df, spot, include_delta=False):
    import pandas as pd
    records, metadata = raw_exposure_records(df.to_dict("records"), spot, include_delta)
    columns = ["strike", "call_gex", "put_gex", "net_gex"] + (["call_dex", "put_dex", "net_dex"] if include_delta else [])
    result = pd.DataFrame(records, columns=columns)
    for col in columns[1:]:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result.attrs.update(metadata)
    return result


def collapse_option_records_by_strike(rows):
    """Consolida rankings/Flow; contratos separados continuam no motor de gregas."""
    grouped = {}
    for row in deduplicate_option_records(rows, ticker_keys=("c_ticker", "p_ticker")):
        grouped.setdefault(row["strike"], []).append(row)
    collapsed = []
    for strike, group in sorted(grouped.items()):
        result = dict(group[0])
        for key in ("c_oi", "p_oi", "cv", "pv"):
            if any(key in row for row in group):
                values = [finite_number(row.get(key)) for row in group]
                result[key] = sum(values) if all(value is not None and value >= 0 for value in values) else None
        for side in ("c", "p"):
            oi = [finite_number(row.get(f"{side}_oi")) for row in group]
            for metric in ("last", "bid", "ask", "delta", "gamma", "theta", "vega", "iv"):
                key = f"{side}_{metric}"
                if not any(key in row for row in group):
                    continue
                values = [finite_number(row.get(key)) for row in group]
                if metric in ("last", "bid", "ask"):
                    result[key] = sum(values) / len(values) if all(value is not None for value in values) else None
                    continue
                total = result.get(f"{side}_oi")
                active = [(weight, value) for weight, value in zip(oi, values) if weight is not None and weight > 0]
                if total is None or any(value is None for _, value in active):
                    result[key] = None
                elif total > 0:
                    result[key] = sum(weight * value for weight, value in active) / total
                else:
                    result[key] = sum(values) / len(values) if all(value is not None for value in values) else None
        collapsed.append(result)
    return collapsed


def collapse_option_rows_by_strike(df: pd.DataFrame) -> pd.DataFrame:
    """Soma séries distintas; Greeks ponderados por OI preservam a exposição."""
    import pandas as pd

    if df.empty or "strike" not in df.columns:
        return df.copy()

    base = df[df["strike"] > 0].copy()
    if base.empty:
        return base

    base = deduplicate_option_rows(base)
    for col in SUM_COLUMNS & set(base.columns):
        base[col] = base[col].map(finite_number)
        if col.endswith(("_oi", "_oi_v")):
            base.loc[base[col] < 0, col] = float("nan")

    agg_map = {}
    for col in base.columns:
        if col == "strike":
            continue
        if col in SUM_COLUMNS:
            agg_map[col] = lambda values: values.sum(min_count=len(values))
        elif col in MEAN_COLUMNS:
            agg_map[col] = "mean"
        elif col.endswith("_ticker"):
            agg_map[col] = "first"
        elif pd.api.types.is_numeric_dtype(base[col]):
            agg_map[col] = "mean"
        else:
            agg_map[col] = "first"

    collapsed = base.groupby("strike", as_index=False).agg(agg_map)
    for side in ("call", "put"):
        oi_col = f"{side}_oi"
        if oi_col not in base:
            continue
        for greek in ("delta", "gamma", "theta", "vega", "iv"):
            col = f"{side}_{greek}"
            if col not in base:
                continue
            weighted = {}
            for strike, group in base.groupby("strike"):
                oi = pd.to_numeric(group[oi_col], errors="coerce")
                values = pd.to_numeric(group[col], errors="coerce")
                positive = oi.gt(0)
                if oi.isna().any() or values[positive].isna().any():
                    weighted[strike] = float("nan")
                    continue
                valid = positive & values.notna()
                total = oi[valid].sum()
                weighted[strike] = (values[valid] * oi[valid]).sum() / total if total > 0 else (
                    float("nan") if greek == "iv" else 0.0
                )
            collapsed[col] = collapsed["strike"].map(weighted)
    return collapsed.sort_values("strike").reset_index(drop=True)


def expiration_inventory(data_dir, tag, reference_date):
    """Fechamentos + Volume do dia + vencimentos ativos da sessão anterior."""
    import os
    import glob
    from bova11_shared import tag_to_iso_nearest, expiry_label_to_iso_nearest
    sessions = {}
    priorities = {}
    for path in glob.glob(os.path.join(str(data_dir), "*.csv")):
        name = os.path.basename(path)
        match = re.match(r"venc (.+?) fechamento \(([^ )]+)( Volume)?\)\.csv$", name) or re.match(r"venc_(.+?)_fechamento__([^_]+)(_Volume)?_\.csv$", name)
        if not match:
            continue
        label, session, volume = match.groups()
        label = label.replace("_", " ")
        iso = tag_to_iso_nearest(session, reference_date)
        if not iso:
            continue
        entry = sessions.setdefault(iso, {}).setdefault(label, {"label":label, "close":None, "volume":None})
        kind = "volume" if volume else "close"
        priority = 3 if session == tag else 2 if "pos" in session.lower() else 0 if "pre" in session.lower() else 1
        key = (iso, label, kind)
        if priority > priorities.get(key, -1):
            priorities[key] = priority
            entry[kind] = path
    current_iso = tag_to_iso_nearest(tag, reference_date)
    if current_iso is None:
        return []
    current = sessions.get(current_iso, {})
    previous = max((iso for iso in sessions if iso < current_iso), default=None)
    expected = dict(current)
    for label, entry in sessions.get(previous, {}).items():
        expiry = expiry_label_to_iso_nearest(label, reference_date)
        if expiry and expiry > reference_date and label not in expected:
            expected[label] = {"label":label, "close":None, "volume":None}
    return [dict(entry, exp_date=expiry_label_to_iso_nearest(label, reference_date))
            for label, entry in sorted(expected.items())]
