"""
שלב 1 — תעבורה (HTTP).
================================================================================
אחריות יחידה: להביא JSON גולמי מ-GuruFocus. שום פירוש של התוכן לא קורה כאן.

עקרונות:
  * אימות לפי התיעוד הרשמי — הדר ``Authorization: <token>`` בלי "Bearer".
  * retry רק על שגיאות זמניות (429/5xx/רשת). 401/403/404 נזרקות מיד —
    אין טעם לשרוף בקשות מהמכסה על שגיאה קבועה.
  * מטמון דיסק: המכסה של GuruFocus מוגבלת. בזמן פיתוח, כל ריצה חוזרת
    קוראת מהדיסק ולא מהרשת.
  * המפתח לעולם לא נכנס ללוג או להודעת שגיאה.

Endpoints בשימוש (אומתו מול ה-API החי):
    GET /stocks/{symbol}/fundamentals -> dict עם annually / quarterly / ttm /
                                          basic_information / stockid
    GET /stocks/{symbol}/profile      -> פרופיל החברה, לרבות sector ו-industry
    GET /stocks/{symbol}/valuations   -> היסטוריית שווי שוק ויחסי שווי וחוב
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.gurufocus.com/data"

# הנתיבים היחידים שהפרוייקט משתמש בהם. אם GuruFocus משנים נתיב — כאן מעדכנים.
PATH_FUNDAMENTALS = "/stocks/{symbol}/fundamentals"
PATH_PROFILE = "/stocks/{symbol}/profile"
PATH_VALUATIONS = "/stocks/{symbol}/valuations"

# קודי HTTP שראוי לנסות שוב עליהם
_RETRYABLE = {429, 500, 502, 503, 504}


class GuruFocusError(RuntimeError):
    """כשל בתקשורת מול ה-API."""


class AuthenticationError(GuruFocusError):
    """המפתח שגוי, פג, או שאין הרשאה ל-endpoint הזה."""


class NotFoundError(GuruFocusError):
    """הטיקר או הנתיב לא קיימים."""


class GuruFocusClient:
    """לקוח דק מול GuruFocus Data API, עם retry ומטמון דיסק אופציונלי."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: int = 60,
        max_retries: int = 3,
        cache_dir: Path | None = None,
        cache_ttl_hours: float = 24.0,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("נדרש מפתח API")
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl_hours = cache_ttl_hours
        self._session = session or requests.Session()
        # מונה בקשות רשת בפועל — שימושי כדי לדעת כמה מהמכסה נצרכה
        self.network_calls = 0
        self.cache_hits = 0

    # -- מטמון ---------------------------------------------------------------
    def _cache_path(self, symbol: str, kind: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = "".join(c for c in symbol if c.isalnum() or c in "._-")
        return self.cache_dir / f"{safe}__{kind}.json"

    def _read_cache(self, path: Path | None) -> Any | None:
        if path is None or not path.exists():
            return None
        if self.cache_ttl_hours > 0:
            age_h = (time.time() - path.stat().st_mtime) / 3600
            if age_h > self.cache_ttl_hours:
                log.debug("מטמון פג (%.1f שעות): %s", age_h, path.name)
                return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("מטמון פגום, מתעלם: %s (%s)", path.name, exc)
            return None

    def _write_cache(self, path: Path | None, payload: Any) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # כתיבה אטומית — קובץ זמני ואז החלפה, כדי לא להשאיר מטמון חלקי
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("כשל בכתיבת מטמון %s: %s", path.name, exc)

    # -- בקשה ----------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> Any:
        """GET עם retry על שגיאות זמניות בלבד."""
        url = f"{self.base_url}{path}"
        last_err = "לא ידוע"

        for attempt in range(1, self.max_retries + 1):
            try:
                self.network_calls += 1
                resp = self._session.get(
                    url,
                    headers={"Authorization": self._api_key},
                    params=params or {},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_err = f"שגיאת רשת: {exc}"
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                break

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    raise GuruFocusError(
                        f"תשובה שאינה JSON מ-{path}: {resp.text[:200]}"
                    ) from None

            if resp.status_code in (401, 403):
                raise AuthenticationError(
                    f"אימות נכשל ({resp.status_code}) ב-{path}. "
                    "בדקו את המפתח ואת ההרשאות למנוי."
                )
            if resp.status_code == 404:
                raise NotFoundError(f"לא נמצא (404): {path}")

            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code in _RETRYABLE and attempt < self.max_retries:
                wait = 2 ** attempt
                log.warning("%s — ניסיון %d/%d, המתנה %ds",
                            last_err, attempt, self.max_retries, wait)
                time.sleep(wait)
                continue
            break

        raise GuruFocusError(f"הבקשה ל-{path} נכשלה — {last_err}")

    def _fetch(self, symbol: str, kind: str, path: str, *, use_cache: bool = True) -> Any:
        """מחזיר JSON, מהמטמון אם אפשר ומהרשת אם צריך."""
        cache_path = self._cache_path(symbol, kind) if use_cache else None
        cached = self._read_cache(cache_path)
        if cached is not None:
            self.cache_hits += 1
            log.info("[%s] %s — מהמטמון", symbol, kind)
            return cached

        log.info("[%s] %s — בקשה ל-API", symbol, kind)
        payload = self._get(path.format(symbol=symbol))
        self._write_cache(cache_path, payload)
        return payload

    # -- API ציבורי ----------------------------------------------------------
    def fundamentals(self, symbol: str, *, use_cache: bool = True) -> dict:
        """הדוחות הכספיים המלאים של טיקר.

        מבנה מאומת:
            {"annually": [...], "quarterly": [...], "ttm": {...},
             "basic_information": {...}, "stockid": "..."}
        """
        payload = self._fetch(symbol, "fundamentals", PATH_FUNDAMENTALS,
                              use_cache=use_cache)
        if not isinstance(payload, dict):
            raise GuruFocusError(
                f"fundamentals עבור {symbol} החזיר {type(payload).__name__} ולא dict"
            )
        return payload

    def profile(self, symbol: str, *, use_cache: bool = True) -> dict:
        """פרופיל החברה, לרבות סקטור ותעשייה.

        במבנה הנוכחי של GuruFocus שדות הסיווג נמצאים תחת ``general``.
        התשובה נשמרת במטמון נפרד בשם ``{symbol}__profile.json``.
        """
        payload = self._fetch(
            symbol,
            "profile",
            PATH_PROFILE,
            use_cache=use_cache,
        )
        if not isinstance(payload, dict):
            raise GuruFocusError(
                f"profile עבור {symbol} החזיר "
                f"{type(payload).__name__} ולא dict"
            )
        return payload

    def valuations(self, symbol: str, *, use_cache: bool = True) -> dict:
        """היסטוריית שווי שוק, נתוני מניה ויחסי שווי וחוב."""
        payload = self._fetch(
            symbol,
            "valuations",
            PATH_VALUATIONS,
            use_cache=use_cache,
        )
        if not isinstance(payload, dict):
            raise GuruFocusError(
                f"valuations עבור {symbol} החזיר "
                f"{type(payload).__name__} ולא dict"
            )
        return payload

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "GuruFocusClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
