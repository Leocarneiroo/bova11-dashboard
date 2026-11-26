#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helpers compartilhados para o ecossistema BOVA11.

Objetivos:
- normalizar parsing BR / tags de data
- persistir e resolver spot manual
- centralizar fórmulas canônicas de exposições
- evitar proxies silenciosos quando o preço não estiver disponível
"""

from __future__ import annotations

import json
import math
import os
import re
import glob
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple


MESES_NUM = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def parse_br_number(raw, none_on_blank: bool = False):
    """Converte número BR com suporte a k/M/%; opcionalmente preserva blanks."""
    if raw is None:
        return None if none_on_blank else 0.0

    if not isinstance(raw, str):
        try:
            if raw != raw:
                return None if none_on_blank else 0.0
            return float(raw)
        except Exception:
            return None if none_on_blank else 0.0

    s = raw.strip().replace("\r", "").rstrip("%")
    if s in ("", "-", "--", "None", "nan"):
        return None if none_on_blank else 0.0

    mult = 1.0
    sl = s.lower()
    if sl.endswith("k"):
        mult = 1_000.0
        s = s[:-1]
    elif sl.endswith("m"):
        mult = 1_000_000.0
        s = s[:-1]

    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s) * mult
    except Exception:
        return None if none_on_blank else 0.0


def parse_b3_close_row(columns, schema_size=None):
    """Normalize legacy 23-column and current 21-column B3 close rows.

    Missing fields remain None. In the 21-column layout, OI and last price
    are absent from close data and must be sourced elsewhere or left unknown.
    """
    schema_size = schema_size or len(columns)
    if schema_size >= 23:
        indexes = {
            "call_ticker": 0, "call_last": 1, "call_oi": 2,
            "call_delta": 3, "call_gamma": 4, "call_theta": 5,
            "call_vega": 6, "call_iv": 7, "call_trades": 8,
            "call_bid": 9, "call_ask": 10, "strike": 11,
            "put_bid": 12, "put_ask": 13, "put_trades": 14,
            "put_iv": 15, "put_vega": 16, "put_theta": 17,
            "put_gamma": 18, "put_delta": 19, "put_oi": 20,
            "put_last": 21, "put_ticker": 22,
        }
    elif schema_size == 21:
        indexes = {
            "call_ticker": 0, "call_delta": 1, "call_gamma": 2,
            "call_theta": 3, "call_vega": 5, "call_iv": 6,
            "call_trades": 7, "call_bid": 8, "call_ask": 9,
            "strike": 10, "put_bid": 11, "put_ask": 12,
            "put_trades": 13, "put_iv": 14, "put_vega": 15,
            "put_theta": 17, "put_gamma": 18, "put_delta": 19,
            "put_ticker": 20,
        }
    else:
        return None

    row = {key: None for key in (
        "call_last", "call_oi", "put_last", "put_oi", "call_rho", "put_rho"
    )}
    for key, index in indexes.items():
        if index >= len(columns):
            continue
        if key.endswith("ticker"):
            row[key] = columns[index].strip()
        else:
            row[key] = parse_br_number(columns[index], none_on_blank=True)
    if schema_size >= 23:
        row["call_rho"] = None
        row["put_rho"] = None
    elif schema_size == 21:
        row["call_rho"] = parse_br_number(columns[4], none_on_blank=True) if len(columns) > 4 else None
        row["put_rho"] = parse_br_number(columns[16], none_on_blank=True) if len(columns) > 16 else None
    return row


def merge_b3_close_open_interest(rows, volume_path: Optional[str]):
    """Fill unknown close OI from the matching B3 Volume export only."""
    if not volume_path or not os.path.exists(volume_path):
        return rows
    try:
        with open(volume_path, "r", encoding="latin-1") as source:
            lines = source.readlines()
    except OSError:
        return rows
    if not lines:
        return rows

    by_ticker = {}
    by_strike = {}
    for line in lines[1:]:
        columns = line.strip().replace("\r", "").split(";")
        if len(columns) < 11:
            continue
        strike = parse_br_number(columns[5], none_on_blank=True)
        call_oi = parse_br_number(columns[2], none_on_blank=True)
        put_oi = parse_br_number(columns[8], none_on_blank=True)
        pair = (columns[0].strip(), columns[10].strip())
        values = (call_oi, put_oi)
        if all(pair):
            by_ticker[pair] = values
        if strike is not None:
            by_strike[strike] = values

    for row in rows:
        pair = (row.get("call_ticker", ""), row.get("put_ticker", ""))
        values = by_ticker.get(pair)
        if values is None and not any(pair):
            values = by_strike.get(row.get("strike"))
        if values is None:
            continue
        for side, value in zip(("call_oi", "put_oi"), values):
            if row.get(side) is None and value is not None:
                row[side] = value
    return rows


def parse_b3_close_csv(path: str, merge_volume: bool = True):
    """Read a B3 close export using either supported schema."""
    path = os.fspath(path)
    try:
        with open(path, "r", encoding="latin-1") as source:
            lines = source.readlines()
    except OSError:
        return []
    if not lines:
        return []
    rows = []
    schema_size = len(lines[0].strip().split(";"))
    for line in lines[1:]:
        columns = line.strip().replace("\r", "").split(";")
        row = parse_b3_close_row(columns, schema_size=schema_size)
        if row and row.get("strike") is not None:
            rows.append(row)
    if merge_volume:
        volume_path = None
        if " Volume).csv" not in path and path.endswith(".csv"):
            if re.search(r"\([^)]+\)\.csv$", path):
                volume_path = re.sub(r"\)\.csv$", " Volume).csv", path)
            elif path.endswith("_.csv"):
                volume_path = path[:-5] + "_Volume_.csv"
        merge_b3_close_open_interest(rows, volume_path)
    return rows

def normalize_tag(tag: str) -> str:
    if not tag:
        return ""
    return re.sub(r"(pos|pre)([a-z]{3})$", r"\2", str(tag).lower())


def tag_sort_key(tag: str) -> Tuple[int, int, str]:
    norm = normalize_tag(tag)
    m = re.match(r"(\d{1,2})([a-z]{3})$", norm)
    if not m:
        return (99, 99, norm)
    return (MESES_NUM.get(m.group(2), 99), int(m.group(1)), norm)


def tag_to_iso(tag: str, year: Optional[int] = None) -> Optional[str]:
    norm = normalize_tag(tag)
    m = re.match(r"(\d{1,2})([a-z]{3})$", norm)
    if not m:
        return None
    use_year = int(year or datetime.now().year)
    month = MESES_NUM.get(m.group(2))
    if month is None:
        return None
    return f"{use_year}-{month:02d}-{int(m.group(1)):02d}"


def _nearest_calendar_date(day: int, month: int, reference_date):
    if isinstance(reference_date, str):
        reference_date = datetime.strptime(reference_date, "%Y-%m-%d").date()
    elif isinstance(reference_date, datetime):
        reference_date = reference_date.date()
    reference_date = reference_date or datetime.now().date()
    candidates = []
    for year in (reference_date.year - 1, reference_date.year, reference_date.year + 1):
        try:
            candidates.append(datetime(year, month, day).date())
        except ValueError:
            continue
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (abs((item - reference_date).days), item > reference_date),
    )


def tag_to_iso_nearest(tag: str, reference_date=None) -> Optional[str]:
    normalized = normalize_tag(tag)
    match = re.fullmatch(r"(\d{1,2})([a-z]{3})", normalized)
    if not match:
        return None
    month = MESES_NUM.get(match.group(2))
    if month is None:
        return None
    resolved = _nearest_calendar_date(int(match.group(1)), month, reference_date)
    return resolved.isoformat() if resolved else None


def discover_date_tags(data_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Retorna tags D/D-1 ordenadas por data real inferida do mtime dos CSVs."""
    patterns = (
        os.path.join(data_dir, "venc_*_fechamento__*_.csv"),
        os.path.join(data_dir, "venc * fechamento (*).csv"),
    )
    metadata = {}
    seen = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
            name = os.path.basename(path)
            lower = name.lower()
            if "volume" in lower or " copy" in lower:
                continue
            match = re.search(r"fechamento(?:__| \()([a-z0-9]+)", name, re.IGNORECASE)
            if not match:
                continue
            tag = match.group(1)
            normalized = normalize_tag(tag)
            parsed = re.fullmatch(r"(\d{1,2})([a-z]{3})", normalized)
            if not parsed:
                continue
            month = MESES_NUM.get(parsed.group(2))
            if month is None:
                continue
            mtime = os.path.getmtime(path)
            reference = datetime.fromtimestamp(mtime).date()
            iso_date = tag_to_iso_nearest(normalized, reference)
            if not iso_date:
                continue
            meta = metadata.setdefault(
                normalized,
                {"count": 0, "mtime": -1, "tag": tag, "date": iso_date},
            )
            meta["count"] += 1
            rank = lambda value: 2 if "pos" in value.lower() else 0 if "pre" in value.lower() else 1
            if mtime > meta["mtime"] or (
                mtime == meta["mtime"] and rank(tag) > rank(meta["tag"])
            ):
                meta.update(mtime=mtime, tag=tag, date=iso_date)

    if not metadata:
        return None, None
    valid = [item for item in metadata.values() if item["count"] >= 2]
    if not valid:
        valid = list(metadata.values())
    ordered = sorted(valid, key=lambda item: (item["date"], item["tag"].lower()))
    return ordered[-1]["tag"], ordered[-2]["tag"] if len(ordered) > 1 else None


def load_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_spot_history(path: str) -> Dict[str, dict]:
    payload = load_json(path, {})
    return payload if isinstance(payload, dict) else {}


def upsert_spot_history(
    path: str,
    ref_date: str,
    ref_tag: str,
    spot: float,
    source: str = "runner_manual",
    timestamp: Optional[str] = None,
) -> Dict[str, dict]:
    history = load_spot_history(path)
    now_iso = timestamp or datetime.now().isoformat(timespec="seconds")
    history[str(ref_date)] = {
        "date": str(ref_date),
        "tag": str(ref_tag),
        "spot": float(spot),
        "source": str(source),
        "timestamp": now_iso,
    }
    save_json(path, history)
    return history


def get_spot_record(
    history: Dict[str, dict],
    ref_date: Optional[str] = None,
    ref_tag: Optional[str] = None,
) -> Optional[dict]:
    if ref_date:
        rec = history.get(str(ref_date))
        if isinstance(rec, dict):
            return rec

    if ref_tag:
        tag_norm = normalize_tag(ref_tag)
        candidates = []
        for rec in history.values():
            if not isinstance(rec, dict):
                continue
            if normalize_tag(rec.get("tag", "")) == tag_norm:
                candidates.append(rec)
        if candidates:
            candidates.sort(key=lambda x: x.get("timestamp", ""))
            return candidates[-1]

    return None


def resolve_spot(
    spot: Optional[float] = None,
    spot_history_file: Optional[str] = None,
    ref_date: Optional[str] = None,
    ref_tag: Optional[str] = None,
    stored_spot: Optional[float] = None,
    fetcher: Optional[Callable[[str], Optional[float]]] = None,
) -> Tuple[Optional[float], str, Optional[str]]:
    """Resolve spot sem inventar proxies silenciosos."""
    if spot is not None and float(spot) > 0:
        return float(spot), "cli", None

    history = load_spot_history(spot_history_file) if spot_history_file else {}
    rec = get_spot_record(history, ref_date=ref_date, ref_tag=ref_tag)
    if rec and rec.get("spot") is not None:
        try:
            val = float(rec["spot"])
            if val > 0:
                return val, f"spot_history:{rec.get('source', 'manual')}", None
        except Exception:
            pass

    if stored_spot is not None:
        try:
            val = float(stored_spot)
            if val > 0:
                return val, "stored_history", None
        except Exception:
            pass

    if fetcher and ref_date:
        try:
            fetched = fetcher(ref_date)
        except Exception as exc:
            fetched = None
            warn = f"Falha ao consultar preço externo: {exc}"
        else:
            warn = None
        if fetched is not None:
            try:
                val = float(fetched)
                if val > 0:
                    return val, "yfinance", warn
            except Exception:
                pass
        return None, "unresolved", warn or f"Preço indisponível para {ref_date}"

    return None, "unresolved", "Preço indisponível — informe spot manual ou forneça histórico confiável."


def build_manual_spot_map(path: Optional[str]) -> Dict[str, float]:
    history = load_spot_history(path) if path else {}
    out: Dict[str, float] = {}
    for key, rec in history.items():
        if not isinstance(rec, dict):
            continue
        try:
            val = float(rec.get("spot"))
        except Exception:
            continue
        if val > 0:
            out[str(key)] = val
    return out


def calc_max_pain(strike_map: Dict[float, dict]) -> Tuple[list, Optional[float], Optional[float]]:
    strikes = sorted(float(s) for s in strike_map.keys() if float(s) > 0)
    if not strikes:
        return [], None, None

    curve = []
    best_strike = None
    best_loss = None
    for test in strikes:
        call_loss = 0.0
        put_loss = 0.0
        for s in strikes:
            row = strike_map[s]
            call_loss += float(row.get("call_oi", 0.0)) * max(test - s, 0.0)
            put_loss += float(row.get("put_oi", 0.0)) * max(s - test, 0.0)
        total_loss = call_loss + put_loss
        curve.append({
            "strike": test,
            "call_loss": call_loss,
            "put_loss": put_loss,
            "total_loss": total_loss,
        })
        if best_loss is None or total_loss < best_loss:
            best_loss = total_loss
            best_strike = test

    return curve, best_strike, best_loss


def calc_gex_components(call_gamma, put_gamma, call_oi, put_oi, spot: float, multiplier: float = 1.0) -> Tuple[float, float, float]:
    """R$ de sensibilidade de hedge por movimento de 1%, sob convenção call+/put−."""
    factor = (float(spot) ** 2) * float(multiplier) * 0.01
    call = float(call_gamma or 0.0) * float(call_oi or 0.0) * factor
    put = -float(put_gamma or 0.0) * float(put_oi or 0.0) * factor
    return call, put, call + put


def calc_dex_components(call_delta, put_delta, call_oi, put_oi, spot: float) -> Tuple[float, float, float]:
    call = float(call_delta or 0.0) * float(call_oi or 0.0) * float(spot)
    put = float(put_delta or 0.0) * float(put_oi or 0.0) * float(spot)
    return call, put, call + put


def calc_tex_components(call_theta, put_theta, call_oi, put_oi, multiplier: float = 1.0) -> Tuple[float, float, float]:
    call = float(call_theta or 0.0) * float(call_oi or 0.0) * multiplier
    put = float(put_theta or 0.0) * float(put_oi or 0.0) * multiplier
    return call, put, call + put


def calc_vex_components(call_vega, put_vega, call_oi, put_oi, multiplier: float = 1.0) -> Tuple[float, float, float]:
    call = float(call_vega or 0.0) * float(call_oi or 0.0) * multiplier
    put = float(put_vega or 0.0) * float(put_oi or 0.0) * multiplier
    return call, put, call + put


def find_profile_roots(profile):
    """Raízes por mudança de sinal de GEX(spot); não cruza lacunas desconhecidas."""
    roots = []
    previous = None
    zero_spots = []
    for row in sorted(profile, key=lambda item: item['spot']):
        value = row.get('gex_net')
        if value is None or not math.isfinite(float(value)):
            previous = None
            zero_spots = []
            continue
        spot, value = float(row['spot']), float(value)
        if value == 0:
            if previous is not None:
                zero_spots.append(spot)
            continue
        if previous is not None and previous[1] * value < 0:
            x0, y0 = previous
            root = sum(zero_spots) / len(zero_spots) if zero_spots else x0 - y0 * (spot - x0) / (value - y0)
            roots.append(root)
        previous = (spot, value)
        zero_spots = []
    return roots


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expiry_label_to_iso(label: str, year: Optional[int] = None) -> Optional[str]:
    if not label:
        return None
    m = re.search(r"(\d{1,2})\s+([a-z]{3})", str(label).lower())
    if not m:
        return None
    month = MESES_NUM.get(m.group(2))
    if month is None:
        return None
    use_year = int(year or datetime.now().year)
    return f"{use_year}-{month:02d}-{int(m.group(1)):02d}"


def expiry_label_to_iso_nearest(label: str, reference_date) -> Optional[str]:
    if not label:
        return None
    match = re.search(r"(\d{1,2})\s+([a-z]{3})", str(label).lower())
    if not match:
        return None
    month = MESES_NUM.get(match.group(2))
    if month is None:
        return None
    resolved = _nearest_calendar_date(int(match.group(1)), month, reference_date)
    return resolved.isoformat() if resolved else None


def _date_from_iso(iso_date: Optional[str]):
    if not iso_date:
        return None
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").date()
    except Exception:
        return None


def _bs_d1_d2(spot: float, strike: float, sigma: float, t: float, r: float = 0.0, q: float = 0.0):
    if spot <= 0 or strike <= 0 or sigma <= 0 or t <= 0:
        return None, None
    root_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + ((r - q) + 0.5 * sigma * sigma) * t) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    return d1, d2


def bs_gamma(spot: float, strike: float, sigma: float, t: float, r: float = 0.0, q: float = 0.0) -> Optional[float]:
    d1, _ = _bs_d1_d2(spot, strike, sigma, t, r, q)
    if d1 is None:
        return None
    return math.exp(-q * t) * norm_pdf(d1) / (spot * sigma * math.sqrt(t))


def bs_delta(
    spot: float,
    strike: float,
    sigma: float,
    t: float,
    option_type: str,
    r: float = 0.0,
    q: float = 0.0,
) -> Optional[float]:
    d1, _ = _bs_d1_d2(spot, strike, sigma, t, r, q)
    if d1 is None:
        return None
    eq = math.exp(-q * t)
    if str(option_type).lower() == "call":
        return eq * norm_cdf(d1)
    return eq * (norm_cdf(d1) - 1.0)


def bs_vanna(spot: float, strike: float, sigma: float, t: float, r: float = 0.0, q: float = 0.0) -> Optional[float]:
    d1, d2 = _bs_d1_d2(spot, strike, sigma, t, r, q)
    if d1 is None or d2 is None or sigma <= 0:
        return None
    return -math.exp(-q * t) * norm_pdf(d1) * d2 / sigma


def bs_theta_per_day(
    spot: float,
    strike: float,
    sigma: float,
    t: float,
    option_type: str,
    r: float = 0.0,
    q: float = 0.0,
    day_basis: float = 365.0,
) -> Optional[float]:
    d1, d2 = _bs_d1_d2(spot, strike, sigma, t, r, q)
    if d1 is None or d2 is None or sigma <= 0 or t <= 0 or day_basis <= 0:
        return None

    eq = math.exp(-q * t)
    front = -(spot * eq * norm_pdf(d1) * sigma) / (2.0 * math.sqrt(t))
    if str(option_type).lower() == "call":
        theta_annual = front + (q * spot * eq * norm_cdf(d1)) - (r * strike * math.exp(-r * t) * norm_cdf(d2))
    else:
        theta_annual = front - (q * spot * eq * norm_cdf(-d1)) + (r * strike * math.exp(-r * t) * norm_cdf(-d2))
    return theta_annual / day_basis


def bs_charm_delta(
    spot: float,
    strike: float,
    sigma: float,
    t: float,
    r: float = 0.0,
    q: float = 0.0,
    option_type: Optional[str] = None,
) -> Optional[float]:
    d1, d2 = _bs_d1_d2(spot, strike, sigma, t, r, q)
    if d1 is None or d2 is None or sigma <= 0 or t <= 0:
        return None
    root_t = math.sqrt(t)
    eq = math.exp(-q * t)
    charm_base = norm_pdf(d1) * ((2.0 * (r - q) * t) - (d2 * sigma * root_t)) / (2.0 * t * sigma * root_t)
    opt = str(option_type or "").lower()
    if opt == "call":
        return -eq * ((-q * norm_cdf(d1)) + charm_base)
    if opt == "put":
        return -eq * ((-q * (norm_cdf(d1) - 1.0)) + charm_base)
    return -eq * charm_base


def calc_convexity_decomposition(
    *,
    strike: float,
    option_type: str,
    spot_d: float,
    spot_d1: float,
    delta_d: float,
    delta_d1: float,
    gamma_d: Optional[float] = None,
    gamma_d1: Optional[float] = None,
    iv_d: Optional[float] = None,
    iv_d1: Optional[float] = None,
    expiry_label: Optional[str] = None,
    session_date_d: Optional[str] = None,
    session_date_d1: Optional[str] = None,
    r: float = 0.0,
):
    """Decompõe ΔDelta em contribuições de gamma, vanna, charm e residual."""
    def _clean(v: Optional[float], eps: float = 1e-12) -> float:
        if v is None:
            return 0.0
        v = float(v)
        return 0.0 if abs(v) < eps else v

    delta_delta = float(delta_d) - float(delta_d1)
    delta_spot = float(spot_d) - float(spot_d1)

    expiry_iso = expiry_label_to_iso_nearest(expiry_label, session_date_d) if session_date_d else expiry_label_to_iso(expiry_label)
    expiry_date = _date_from_iso(expiry_iso)
    sess_d = _date_from_iso(session_date_d)
    sess_d1 = _date_from_iso(session_date_d1)

    dte_d = None
    dte_d1 = None
    if expiry_date and sess_d:
        dte_d = max((expiry_date - sess_d).days, 0) / 365.0
    if expiry_date and sess_d1:
        dte_d1 = max((expiry_date - sess_d1).days, 0) / 365.0

    valid_dtes = [v for v in (dte_d, dte_d1) if v is not None and v > 0]
    t_ref = max(sum(valid_dtes) / len(valid_dtes), 1.0 / 365.0) if valid_dtes else None
    delta_t = (dte_d - dte_d1) if (dte_d is not None and dte_d1 is not None) else None

    valid_sigmas = [v / 100.0 for v in (iv_d, iv_d1) if v is not None and v > 0]
    sigma_ref = sum(valid_sigmas) / len(valid_sigmas) if valid_sigmas else None
    delta_sigma = ((iv_d - iv_d1) / 100.0) if (iv_d is not None and iv_d1 is not None) else None

    spot_ref = max((float(spot_d) + float(spot_d1)) / 2.0, 1e-9)
    gamma_ref = None
    valid_gammas = [g for g in (gamma_d, gamma_d1) if g is not None]
    if valid_gammas:
        gamma_ref = sum(valid_gammas) / len(valid_gammas)
    elif sigma_ref is not None and t_ref is not None:
        gamma_ref = bs_gamma(spot_ref, float(strike), sigma_ref, t_ref, r)

    vanna_ref = None
    charm_ref = None
    if sigma_ref is not None and t_ref is not None:
        vanna_ref = bs_vanna(spot_ref, float(strike), sigma_ref, t_ref, r)
        charm_ref = bs_charm_delta(spot_ref, float(strike), sigma_ref, t_ref, r, option_type=option_type)

    gamma_contrib = _clean((gamma_ref or 0.0) * delta_spot)
    vanna_contrib = _clean((vanna_ref or 0.0) * delta_sigma if delta_sigma is not None else 0.0)
    # Charm é dDelta/dtempo de calendário; delta_t mede tempo restante.
    charm_contrib = _clean(-(charm_ref or 0.0) * delta_t if delta_t is not None else 0.0)
    residual = _clean(delta_delta - gamma_contrib - vanna_contrib - charm_contrib)

    return {
        "option_type": str(option_type).upper(),
        "delta_delta": delta_delta,
        "delta_spot": delta_spot,
        "delta_sigma": delta_sigma,
        "delta_t": delta_t,
        "dte_d": dte_d,
        "dte_d1": dte_d1,
        "t_ref": t_ref,
        "gamma_ref": _clean(gamma_ref),
        "vanna_ref": _clean(vanna_ref),
        "charm_ref": _clean(charm_ref),
        "gamma_contrib": gamma_contrib,
        "vanna_contrib": vanna_contrib,
        "charm_contrib": charm_contrib,
        "residual": residual,
        "abs_gamma": abs(gamma_contrib),
        "abs_vanna": abs(vanna_contrib),
        "abs_charm": abs(charm_contrib),
        "abs_residual": abs(residual),
    }
