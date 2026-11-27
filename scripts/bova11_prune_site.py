"""Poda o site gerado antes da publicação, para o repositório e o Pages ficarem leves.

Mantém as N sessões mais recentes de cada série datada e remove artefatos de debug/teste.
As páginas do run atual nunca são removidas, porque são as mais recentes por data.
"""

import re
import sys
from pathlib import Path

DATED_FAMILIES = ("gex_pro", "rankings_pro", "insights", "trade_score", "tex_dex_vex", "convexity")
KEEP_SESSIONS = 5

_SESSION_NAME = re.compile(r"^bova11_[a-z_]+_(\d{1,2})([a-z]{3})_vs_(\d{1,2})([a-z]{3})\.html$")
_MONTHS = {
    month: index
    for index, month in enumerate(
        ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"),
        start=1,
    )
}


def session_key(path: Path):
    """Data da sessão (mês, dia do D) extraída do nome; nomes fora do padrão vão para o fim."""
    match = _SESSION_NAME.match(path.name.lower())
    if not match:
        return (0, 0)
    month = _MONTHS.get(match.group(4))
    if month is None:
        return (0, 0)
    return (month, int(match.group(3)))


def prune_site(output_dir, keep_sessions: int = KEEP_SESSIONS) -> list:
    root = Path(output_dir)
    removed = []

    for pattern in ("_debug_*.html", "*_test*.html"):
        for path in root.glob(pattern):
            path.unlink()
            removed.append(path.name)

    for family in DATED_FAMILIES:
        files = sorted(root.glob(f"bova11_{family}_*.html"), key=session_key, reverse=True)
        for path in files[keep_sessions:]:
            path.unlink()
            removed.append(path.name)

    reports = {}
    for path in root.glob("bova11_tex_dex_vex_*.expiry-*.js"):
        report = root / (path.name.split(".expiry-", 1)[0] + ".html")
        if report not in reports:
            reports[report] = report.read_text(encoding="utf-8") if report.is_file() else ""
        if f'"{path.name}"' not in reports[report]:
            path.unlink()
            removed.append(path.name)
    return removed


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    removed = prune_site(target)
    for name in removed:
        print(f"  removido: {name}")
    print(f"  {len(removed)} arquivo(s) podados de {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
