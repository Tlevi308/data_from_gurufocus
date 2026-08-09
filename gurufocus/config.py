"""
שלב 0 — הגדרות וסודות.
================================================================================
אחריות יחידה: לייצר אובייקט Settings מאומת מתוך config.yaml + משתני סביבה.
אין כאן רשת, אין pandas, אין לוגיקה עסקית.

סדר עדיפות למפתח ה-API:
    1. משתנה סביבה GURUFOCUS_API_KEY
    2. קובץ .env בשורש הפרוייקט
    3. הזנה ידנית מוסתרת (getpass) — רק בהרצה אינטראקטיבית

המפתח לעולם לא נשמר בקוד ולא נכתב ללוג.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields as dataclass_fields, replace
from pathlib import Path
from typing import Any

from .decomposition import DEFAULT_TOLERANCE, DecompositionTolerance
from .wacc import DEFAULT_ASSUMPTIONS, WaccAssumptions

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# תווים/מחרוזות שמעידות שהמפתח הוא placeholder ולא מפתח אמיתי
_PLACEHOLDERS = {
    "", "key", "token", "paste_your_key_here", "your_api_key", "your_token_here",
    "changeme", "xxx",
}


# ---------------------------------------------------------------------------
# מבני ההגדרות
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NetworkSettings:
    timeout_seconds: int = 60
    max_retries: int = 3
    sleep_between_tickers: float = 0.3


@dataclass(frozen=True)
class CacheSettings:
    enabled: bool = True
    directory: Path = PROJECT_ROOT / "data" / "raw"
    ttl_hours: float = 24.0


@dataclass(frozen=True)
class OutputSettings:
    directory: Path = PROJECT_ROOT / "output"
    formats: tuple[str, ...] = ("excel", "csv")


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    tickers: tuple[str, ...] = ("AAPL",)
    period: str = "quarterly"
    # חיתוך על תאריך *סוף התקופה*. end_date ריק = עד התקופה האחרונה שקיימת.
    start_date: str = "1950-01-01"
    end_date: str = ""
    quarter_shift_months: int = 2
    network: NetworkSettings = field(default_factory=NetworkSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    # ספי המהותיות של פירוק ROIC/NOPAT. ברירת המחדל מוגדרת במודול
    # decomposition כדי שהמשמעות הכלכלית של כל סף תתועד לצד החישוב.
    decomposition: DecompositionTolerance = field(
        default_factory=lambda: DEFAULT_TOLERANCE
    )
    # הריבית חסרת הסיכון ופרמיית הסיכון. משתנות עם השוק ולכן יושבות בראש
    # config.yaml, בחלק שמשנים ביום-יום.
    wacc: WaccAssumptions = field(default_factory=lambda: DEFAULT_ASSUMPTIONS)

    # -- נגזרות --------------------------------------------------------------
    @property
    def is_quarterly(self) -> bool:
        return self.period == "quarterly"

    def masked_key(self) -> str:
        """ייצוג בטוח להדפסה/לוג — לעולם לא את המפתח המלא."""
        if not self.api_key:
            return "<לא הוגדר>"
        return f"{self.api_key[:4]}…{self.api_key[-4:]} (אורך {len(self.api_key)})"

    def validate(self) -> None:
        if self.period not in ("quarterly", "annually"):
            raise ValueError(
                f"period חייב להיות 'quarterly' או 'annually' — התקבל {self.period!r}. "
                "שימו לב: ב-API של GuruFocus התקופה השנתית נקראת 'annually' ולא 'annuals'."
            )
        if not self.tickers:
            raise ValueError("לא הוגדרו טיקרים")
        if not self.api_key:
            raise ValueError("לא נמצא מפתח API")
        if self.api_key.strip().lower() in _PLACEHOLDERS:
            raise ValueError("מפתח ה-API הוא placeholder ולא מפתח אמיתי")
        if not 0 <= self.quarter_shift_months <= 11:
            raise ValueError("quarter_shift_months חייב להיות בטווח 0-11")
        if self.end_date and self.end_date < self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) מוקדם מ-start_date ({self.start_date})"
            )
        self.decomposition.validate()
        self.wacc.validate()


# ---------------------------------------------------------------------------
# טעינה
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    """טוען .env פשוט אל os.environ בלי תלות ב-python-dotenv.

    לא דורס משתני סביבה קיימים — סביבה תמיד גוברת על קובץ.

    ⚠️ נקרא ב-utf-8-sig במכוון: עורכים ב-Windows (ובפרט
    ``Set-Content -Encoding utf8`` ב-PowerShell 5.1) כותבים BOM בתחילת הקובץ.
    בלי זה שם המשתנה הראשון נקרא כ-'\\ufeffGURUFOCUS_API_KEY' והמפתח
    "נעלם" בלי שום הודעת שגיאה.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _can_prompt() -> bool:
    """האם באמת יש אדם מול המסך.

    דורש ש*גם* הקלט וגם הפלט יהיו טרמינל. בדיקת stdin בלבד לא מספיקה:
    ב-PowerShell לא-אינטראקטיבי ``sys.stdin.isatty()`` מחזיר True, וסקריפט
    שנשען עליה בלבד ייתקע לנצח בהמתנה להקלדה בריצה אוטומטית.
    ניתן לכבות מפורשות עם GURUFOCUS_NO_PROMPT=1.
    """
    import sys

    if os.environ.get("GURUFOCUS_NO_PROMPT", "").strip().lower() in ("1", "true", "yes"):
        return False
    streams = (sys.stdin, sys.stdout)
    return all(s is not None and hasattr(s, "isatty") and s.isatty() for s in streams)


def resolve_api_key(*, allow_prompt: bool = True) -> str:
    """מאתר את מפתח ה-API לפי סדר העדיפות המתועד למעלה."""
    _load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("GURUFOCUS_API_KEY", "").strip()
    if key:
        return key
    if allow_prompt and _can_prompt():
        import getpass
        return getpass.getpass("GuruFocus API token: ").strip()
    return ""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "נדרש PyYAML כדי לקרוא config.yaml — התקינו: pip install PyYAML"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _resolve_path(value: str | Path) -> Path:
    """נתיב יחסי מפורש ביחס לשורש הפרוייקט, לא לתיקיית העבודה."""
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _reject_unknown_keys(raw: dict[str, Any], block: str, default: Any) -> None:
    """עוצר על מפתח שאינו שדה של ה-dataclass.

    פרמטר שנכתב בשגיאת כתיב היה מתעלם בשקט ומשאיר את ברירת המחדל, כך
    שהתוצאות היו משתנות בלי שאיש ישים לב.
    """
    known = {spec.name for spec in dataclass_fields(type(default))}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"מפתחות לא מוכרים בבלוק {block}: "
            + ", ".join(unknown)
            + " — המפתחות האפשריים: "
            + ", ".join(sorted(known))
        )


def _numeric_block(raw: dict[str, Any], block: str, default: Any) -> Any:
    """בונה dataclass של פרמטרים מספריים מבלוק ב-config.yaml."""
    if not raw:
        return default
    _reject_unknown_keys(raw, block, default)
    try:
        values = {name: float(value) for name, value in raw.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ערך לא מספרי בבלוק {block}: {exc}") from exc
    return type(default)(**values)


def load_settings(
    config_path: str | Path | None = None,
    *,
    allow_prompt: bool = True,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """בונה Settings מ-config.yaml, ממשתני סביבה ומ-overrides של ה-CLI.

    overrides גובר על הקובץ, והקובץ גובר על ברירות המחדל שבקוד.
    """
    path = _resolve_path(config_path or "config.yaml")
    raw = _read_yaml(path)

    net = raw.get("network") or {}
    cache = raw.get("cache") or {}
    out = raw.get("output") or {}
    align = raw.get("alignment") or {}
    decomposition = raw.get("decomposition") or {}
    wacc = raw.get("wacc") or {}

    settings = Settings(
        api_key=resolve_api_key(allow_prompt=allow_prompt),
        tickers=tuple(str(t).strip().upper() for t in raw.get("tickers", ["AAPL"])),
        period=str(raw.get("period", "quarterly")).strip(),
        start_date=str(raw.get("start_date", "1950-01-01")),
        end_date=str(raw.get("end_date") or ""),
        quarter_shift_months=int(align.get("quarter_shift_months", 2)),
        network=NetworkSettings(
            timeout_seconds=int(net.get("timeout_seconds", 60)),
            max_retries=int(net.get("max_retries", 3)),
            sleep_between_tickers=float(net.get("sleep_between_tickers", 0.3)),
        ),
        cache=CacheSettings(
            enabled=bool(cache.get("enabled", True)),
            directory=_resolve_path(cache.get("directory", "data/raw")),
            ttl_hours=float(cache.get("ttl_hours", 24)),
        ),
        output=OutputSettings(
            directory=_resolve_path(out.get("directory", "output")),
            formats=tuple(str(f).lower() for f in out.get("formats", ["excel", "csv"])),
        ),
        decomposition=_numeric_block(
            decomposition, "decomposition", DEFAULT_TOLERANCE
        ),
        wacc=_numeric_block(wacc, "wacc", DEFAULT_ASSUMPTIONS),
    )

    if overrides:
        clean = {k: v for k, v in overrides.items() if v is not None}
        if "tickers" in clean:
            clean["tickers"] = tuple(str(t).strip().upper() for t in clean["tickers"])
        if "cache" in clean and isinstance(clean["cache"], dict):
            clean["cache"] = replace(settings.cache, **clean["cache"])
        if "output" in clean and isinstance(clean["output"], dict):
            clean["output"] = replace(settings.output, **clean["output"])
        settings = replace(settings, **clean)

    return settings
