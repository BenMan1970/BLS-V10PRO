"""
BLUESTAR ENGINE v10 — Hybrid Absolute/Cross-Sectional (V4 architecture)
========================================================================
Single-file monolithic engine. Source of truth = merged JSON.

Pipeline : Pydantic, mode dégradé, calendrier tiéré, ATR synthétique,
preflight, audit trail, 7 facteurs V4, moyenne absolue -> conviction,
quantile -> tie-break/diversification.

Rendu : PDF natif via WeasyPrint si disponible (optionnel, jamais
bloquant) ; sinon HTML calibré A4 imprimable via le navigateur.

Usage:
  python ENGINE.V10.py --merged merge.json --calendar-json calendar.json -o report.html
  python ENGINE.V10.py --merged merge.json --calendar-json calendar.json --pdf report.pdf
  from ENGINE.V10 import run_pipeline, render_pdf   # API (nom de module à adapter)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

import jinja2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Fuseau d'affichage unique, DST-aware (Europe/Paris). Repli défensif sur
# l'ancien offset fixe si tzdata est absente (Windows sans paquet tzdata).
try:
    REPORT_TZ: tzinfo = ZoneInfo("Europe/Paris")
except Exception:  # noqa: BLE001
    REPORT_TZ = timezone(timedelta(hours=1))

logger = logging.getLogger("bluestar.v10")

# Bump manuel à chaque changement de comportement de grading/scoring.
# app.py lit cet attribut via getattr(mod, "__version__", "inconnu").
__version__ = "10.2.9"  # 10.2.9 : nettoyage code mort + finalisation déploiement
                        #   cloud-safe (import WeasyPrint optionnel avec raison
                        #   d'échec exposée, render_pdf ne lève plus, version
                        #   passée au template, coquilles corrigées).
                        # 10.2.7 : patches E (badge SR granulaire), F (fuseau
                        #   DST-aware), G (troncature flux calendaire).
                        # 04/08/2026 : fix CALENDAR_STALE (deux conditions
                        #   opposées, jamais abs()) + PATCH-CALCOVERAGE (export
                        #   JSON structuré calendar-coverage, additif).

# ════════════════════════════════════════════════════════════════════════════
# SECTION 0 — Optional PDF backend (never blocking at import)
# ════════════════════════════════════════════════════════════════════════════
# Sur Streamlit Community Cloud, WeasyPrint est absent (libs natives
# libpango/libcairo non installables). On mémorise la raison exacte de
# l'échec pour que la couche UI puisse l'afficher.
try:  # pragma: no cover
    from weasyprint import HTML as _WeasyHTML  # type: ignore[import-untyped]
    _HAS_WEASYPRINT = True
    _WEASYPRINT_ERROR = ""
except Exception as _wp_exc:  # noqa: BLE001
    _HAS_WEASYPRINT = False
    _WEASYPRINT_ERROR = f"{type(_wp_exc).__name__}: {_wp_exc}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 1 — ENUMS
# ════════════════════════════════════════════════════════════════════════════
class Direction(str, Enum):
    BULLISH = "Bullish"
    BEARISH = "Bearish"
    NEUTRAL = "Neutral"


class ImpactLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventTier(str, Enum):
    S = "S"      # NFP, FOMC, CPI, Rate Decision
    A = "A"      # GDP, PMI, ADP, PCE
    B = "B"      # Speeches / press conf
    NONE = "NONE"


class GateCode(str, Enum):
    PASS = "PASS"  # nosec B105 — état métier Enum, pas un mot de passe
    G0_SCHEMA_ASSET_ERROR = "SCHEMA_ASSET_ERROR"
    G1_CAL_BLACKOUT = "CAL_BLACKOUT"
    G2_LOW_QUALITY = "LOW_QUALITY"
    G3_NO_DIRECTION = "NO_DIRECTION"
    G4_LOW_CONSENSUS = "LOW_CONSENSUS"
    G5_NO_ATR = "NO_ATR"


class Conviction(str, Enum):
    AAA = "AAA"
    AA = "AA"
    A = "A"
    BBB = "BBB"
    BB = "BB"
    B = "B"


class CalStatus(str, Enum):
    OK = "OK"
    BLACKOUT = "BLACKOUT"
    PROXIMITY = "PROXIMITY"
    WATCH = "WATCH"


class MacroRegime(str, Enum):
    """P1-C — régime macro au niveau PORTEFEUILLE (pas par actif)."""
    EVENT_VACUUM = "EVENT_VACUUM"
    EVENT_DRIFT = "EVENT_DRIFT"
    PRE_POLICY_COMPRESSION = "PRE_POLICY_COMPRESSION"
    POST_POLICY_REPRICING = "POST_POLICY_REPRICING"
    UNKNOWN = "UNKNOWN"


class FreshnessAudit(str, Enum):
    """P1-B — réconciliation candles_elapsed vs signal_time."""
    FRESH = "FRESH"        # vérifié cohérent
    STALE = "STALE"        # vérifié INCOHÉRENT
    UNKNOWN = "UNKNOWN"    # non vérifiable -> comportement inchangé


# Ordinal rank for diversification preference (higher = stronger conviction).
_CONVICTION_ORDINAL: Mapping[str, int] = MappingProxyType({
    "AAA": 6, "AA": 5, "A": 4, "BBB": 3, "BB": 2, "B": 1,
})


# ════════════════════════════════════════════════════════════════════════════
# SECTION 2 — HELPERS
# ════════════════════════════════════════════════════════════════════════════
def _dir_eq(a: Any, b: Any) -> bool:
    av = a.value if hasattr(a, "value") else str(a)
    bv = b.value if hasattr(b, "value") else str(b)
    return av.lower() == bv.lower()


def _norm_dir(v: Any) -> Direction:
    if isinstance(v, Direction):
        return v
    s = str(v).lower()
    if "bull" in s:
        return Direction.BULLISH
    if "bear" in s:
        return Direction.BEARISH
    return Direction.NEUTRAL


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None  # noqa: PLR0124
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    if x != x:
        return 0.0
    return max(0.0, min(1.0, x))


def _opposite_dir(d: Direction) -> Direction:
    if d is Direction.BULLISH:
        return Direction.BEARISH
    if d is Direction.BEARISH:
        return Direction.BULLISH
    return Direction.NEUTRAL


def _median(values: Iterable[float]) -> float:
    """Médiane pure-python déterministe. 0.0 sur séquence vide."""
    s = sorted(float(v) for v in values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    """ISO-8601 (suffixe Z toléré) -> datetime aware UTC. None si échec."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
    try:
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ── P1-B : comptage de bougies FX-aware ─────────────────────────────────────
# Semaine FX : dimanche 21:00 UTC -> vendredi 21:00 UTC.
_FX_WEEK_CLOSE_WEEKDAY = 4        # Friday (Monday=0)
_FX_WEEK_CLOSE_HOUR = 21
_FX_WEEK_CLOSED_HOURS = 48.0

_TF_ACTIVE_HOURS: Mapping[str, float] = MappingProxyType({
    "M15": 0.25, "M30": 0.5, "H1": 1.0, "H4": 4.0,
    "D1": 24.0, "DAILY": 24.0,
    "W1": 120.0, "WEEKLY": 120.0,
    "MN": 480.0, "MONTHLY": 480.0,
})


def _fx_active_hours(start: datetime, end: datetime) -> float:
    """Heures de marché FX ouvertes entre start et end (week-ends exclus)."""
    if end <= start:
        return 0.0
    total = (end - start).total_seconds() / 3600.0
    anchor = start.replace(hour=_FX_WEEK_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    anchor -= timedelta(days=(anchor.weekday() - _FX_WEEK_CLOSE_WEEKDAY) % 7)
    if anchor > start:
        anchor -= timedelta(days=7)
    closed = 0.0
    cur = anchor
    while cur < end:
        ov_s = max(cur, start)
        ov_e = min(cur + timedelta(hours=_FX_WEEK_CLOSED_HOURS), end)
        if ov_e > ov_s:
            closed += (ov_e - ov_s).total_seconds() / 3600.0
        cur += timedelta(days=7)
    return max(0.0, total - closed)


def _elapsed_bars_fx(start: datetime, end: datetime, timeframe: str) -> int:
    """Bougies CLOSES entre start et end pour le TF donné. -1 si TF inconnu."""
    bar_h = _TF_ACTIVE_HOURS.get((timeframe or "").upper())
    if not bar_h or bar_h <= 0:
        return -1
    return int(_fx_active_hours(start, end) / bar_h)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CALENDAR MODELS
# ════════════════════════════════════════════════════════════════════════════
_TIER_S = ("non-farm", "nonfarm", "nfp", "fomc", "cpi", "cash rate",
           "bank rate", "rate statement", "interest rate", "monetary policy",
           "funds rate", "policy rate")
_TIER_A = ("gdp", "pmi", "adp", "pce", "employment change", "unemployment",
           "average hourly", "retail sales", "ppi")
_TIER_B = ("speaks", "speech", "press conference", "testifies", "testimony")


def classify_tier(name: str) -> EventTier:
    n = (name or "").lower()
    if any(k in n for k in _TIER_S):
        return EventTier.S
    if any(k in n for k in _TIER_A):
        return EventTier.A
    if any(k in n for k in _TIER_B):
        return EventTier.B
    return EventTier.NONE


def classify_impact(name: str) -> ImpactLevel:
    return ImpactLevel.HIGH if classify_tier(name) != EventTier.NONE else ImpactLevel.MEDIUM


# (hours_before, hours_after) blackout windows by tier
TIER_WINDOWS: Mapping[EventTier, tuple[float, float]] = MappingProxyType({
    EventTier.S: (4.0, 48.0),
    EventTier.A: (2.0, 24.0),
    EventTier.B: (1.0, 6.0),
})
PROXIMITY_MAX_H = 48.0
WATCH_MAX_H = 168.0

DEFAULT_TIER_WINDOW = (2.0, 24.0)

# P0-A — seuils de détection de l'incohérence de fuseau.
CAL_TIME_TOL_H = 0.25         # tolérance individuelle (15 min)
CAL_TIME_MIN_RATIO = 0.80     # part d'événements concordants requise
CALENDAR_STALE_TOL_H = 0.25   # calendrier ANTÉRIEUR au Desk (seuil métier)
MERGE_STALE_TOL_H = 0.25      # merge ANTÉRIEUR au calendrier

# Devises réellement traitées par le desk (vs jambes-instruments : US30,
# NAS100, SPX500, DE30, XAU — un indice n'a pas de calendrier propre).
_DESK_CURRENCIES = frozenset({"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"})


class CalendarEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    currency: str = Field(..., min_length=3, max_length=3)
    event_name: str = Field(..., max_length=256)
    datetime_utc: datetime
    impact: Optional[ImpactLevel] = None
    tier: EventTier = EventTier.NONE
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    hours_until: Optional[float] = None    # pré-calculé par Module 04, audit uniquement
    priority: Optional[str] = None         # CRITICAL/HIGH/MEDIUM/PAST — audit uniquement

    @field_validator("currency")
    @classmethod
    def _up(cls, v: str) -> str:
        return v.upper()

    @field_validator("datetime_utc")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _derive(self) -> "CalendarEvent":
        if self.tier is EventTier.NONE:
            self.tier = classify_tier(self.event_name)
        if self.impact is None:
            self.impact = classify_impact(self.event_name)
        return self


class CalendarSets(BaseModel):
    model_config = ConfigDict(extra="ignore")
    blackout: list[CalendarEvent] = Field(default_factory=list)
    proximity: list[CalendarEvent] = Field(default_factory=list)
    watch: list[CalendarEvent] = Field(default_factory=list)
    suspended_ccy: set[str] = Field(default_factory=set)
    proximity_ccy: set[str] = Field(default_factory=set)
    watch_ccy: set[str] = Field(default_factory=set)
    time_degraded: bool = False
    time_offset_hours: float = 0.0
    # Le flag feed_horizon_truncated doit atteindre f7_macro pour déclencher
    # le fail-closed sur un flux tronqué ou vide.
    feed_horizon_truncated: bool = False
    covered_currencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sets(self) -> "CalendarSets":
        self.suspended_ccy = {e.currency for e in self.blackout}
        self.proximity_ccy = {e.currency for e in self.proximity}
        self.watch_ccy = {e.currency for e in self.watch}
        return self


class CalendarData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    events: list[CalendarEvent] = Field(default_factory=list)
    timezone_source: str = "UTC"
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_html_hash: str = ""
    time_degraded: bool = False
    time_offset_hours: float = 0.0
    time_audit_detail: str = ""
    reachable: bool = True
    feed_horizon_truncated: bool = False
    feed_horizon_h: Optional[float] = None
    feed_coverage_detail: str = ""
    # Fraîcheur — DEUX conditions OPPOSÉES, jamais agrégées par abs().
    stale: bool = False              # CALENDAR_STALE : calendrier antérieur au Desk
    stale_age_h: float = 0.0
    stale_detail: str = ""
    merge_stale: bool = False        # MERGE_STALE : snapshot marché antérieur au calendrier
    merge_stale_age_h: float = 0.0
    merge_stale_detail: str = ""
    covered_currencies: list[str] = Field(default_factory=list)
    # Borne haute réelle du flux, calculée dans load_calendar(). Additif :
    # ne sert qu'à l'export JSON calendar-coverage, aucune décision n'en dépend.
    feed_end_utc: Optional[datetime] = None

    def bucket(self, now: datetime) -> CalendarSets:
        """Si time_degraded, toutes les fenêtres sont élargies de |offset|
        DES DEUX CÔTÉS (fail-closed : les deux champs temporels du feed se
        contredisent, rien ne prouve lequel est correct)."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        pad = abs(self.time_offset_hours) if self.time_degraded else 0.0
        prox_max = PROXIMITY_MAX_H + pad
        watch_max = WATCH_MAX_H + pad
        blackout, proximity, watch = [], [], []
        for ev in self.events:
            if ev.impact != ImpactLevel.HIGH:
                continue
            before, after = TIER_WINDOWS.get(ev.tier, DEFAULT_TIER_WINDOW)
            before += pad
            after += pad
            delta = (ev.datetime_utc - now).total_seconds() / 3600.0
            if -after <= delta <= before:
                blackout.append(ev)
            elif before < delta <= prox_max:
                proximity.append(ev)
            elif prox_max < delta <= watch_max:
                watch.append(ev)
        return CalendarSets(blackout=blackout, proximity=proximity, watch=watch,
                            time_degraded=self.time_degraded,
                            time_offset_hours=self.time_offset_hours,
                            feed_horizon_truncated=self.feed_horizon_truncated,
                            covered_currencies=list(self.covered_currencies))


def audit_calendar_time_consistency(
    events_raw: list[dict],
    generated_at: Optional[datetime],
    tol_h: float = CAL_TIME_TOL_H,
) -> tuple[float, int, int]:
    """P0-A — compare hours_until déclaré et (datetime_utc - generated_at).
    Retourne (offset_median_h, n_concordants, n_verifiables). MESURE seulement.
    """
    if not generated_at:
        return 0.0, 0, 0
    offsets: list[float] = []
    for ev in events_raw or []:
        hu = _safe_float(ev.get("hours_until"))
        dt = _parse_iso_utc(ev.get("datetime_utc"))
        if hu is None or dt is None:
            continue
        offsets.append(hu - (dt - generated_at).total_seconds() / 3600.0)
    if not offsets:
        return 0.0, 0, 0
    med = _median(offsets)
    return med, sum(1 for o in offsets if abs(o - med) <= tol_h), len(offsets)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CANONICAL ASSET VIEW
# ════════════════════════════════════════════════════════════════════════════
class MTFView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pct: int = 0
    direction: Direction = Direction.NEUTRAL
    quality: str = ""
    nc: int = 0
    age_d1: Optional[int] = 0   # null-safe: gap_open assets send None
    atr_h1: Optional[float] = None
    atr_h4: Optional[float] = None
    atr_daily: Optional[float] = None
    biases: dict[str, str] = Field(default_factory=dict)

    @field_validator("direction", mode="before")
    @classmethod
    def _d(cls, v: Any) -> Direction:
        return _norm_dir(v)


class StructureEventView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    signal_id: str = ""
    kind: str = ""
    direction: Direction = Direction.NEUTRAL
    timeframe: str = ""
    level: Optional[float] = None
    confluence_score: float = 0.0
    status: str = ""
    distance_pct: Optional[float] = None
    distance_atr_multiple: Optional[float] = None
    volatility: str = ""
    force: str = ""
    bb_regime: str = "Normal"
    session: str = ""
    candles_elapsed: int = 999
    signal_time: Optional[datetime] = None  # P1-B

    @field_validator("direction", mode="before")
    @classmethod
    def _d(cls, v: Any) -> Direction:
        return _norm_dir(v)

    @field_validator("signal_time")
    @classmethod
    def _tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return None
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


class ZoneView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    level: float
    side: str = ""
    score: float = 0.0
    weighted_score: float = 0.0
    distance_pct: float = 999.0
    timeframes: list[str] = Field(default_factory=list)
    has_weekly: bool = False
    has_daily: bool = False
    has_h4: bool = False


class CanonicalAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str
    base: str = ""
    quote: Optional[str] = None
    asset_class: str = "forex"
    current_price: Optional[float] = None
    # Déclaré explicitement pour ne pas être jeté par extra="ignore".
    current_price_source: Optional[str] = None  # "stale", "live", etc.
    rsi_by_tf: dict[str, dict] = Field(default_factory=dict)
    rsi_h4_status: Optional[str] = None
    mtf: Optional[MTFView] = None
    zones: list[ZoneView] = Field(default_factory=list)
    structure_events: list[StructureEventView] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    atr_effective: Optional[float] = None
    atr_source: Optional[str] = None
    nearest_aligned_zone: Optional[ZoneView] = None
    hot_zone_primary: Optional[ZoneView] = None
    conviction_cap: Optional[str] = None  # ex. "BBB" pour ATR synthétique
    # v3.5.0: produced by merge engine, read-only here. None si absent/crash.
    market_context: Optional[dict[str, Any]] = None


class MergeMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = ""
    assets_count: int = 0
    signals_count: int = 0

    @field_validator("generated_at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


class Clock(BaseModel):
    now_utc: datetime
    now_local: datetime
    date_hdr: str

    # ClassVar OBLIGATOIRE : sans ça, Pydantic v2 traiterait `_REPORT_TZ`
    # comme un attribut privé et cls._REPORT_TZ rendrait un ModelPrivateAttr.
    _REPORT_TZ: ClassVar[tzinfo] = REPORT_TZ

    @classmethod
    def from_meta(cls, generated_at: datetime) -> "Clock":
        now_utc = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=timezone.utc)
        now_local = now_utc.astimezone(cls._REPORT_TZ)
        # Étiquette = fuseau RÉEL (CET/CEST). Scoring ancré sur now_utc.
        tz_label = now_local.tzname() or "CET"
        return cls(now_utc=now_utc, now_local=now_local,
                   date_hdr=f"{now_local.strftime('%Y-%m-%d %H:%M')} {tz_label}")


# ════════════════════════════════════════════════════════════════════════════
# SECTION 5 — V4 MODELS & DATACLASSES
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ScoredFactor:
    name: str
    raw: Optional[float]
    score: float            # bounded [0,1]
    is_missing: bool
    detail: str


@dataclass
class FactorVector:
    symbol: str
    factors: dict[str, ScoredFactor]

    @property
    def present(self) -> list[str]:
        return [n for n, f in self.factors.items() if not f.is_missing]

    @property
    def missing(self) -> list[str]:
        return [n for n, f in self.factors.items() if f.is_missing]

    @property
    def absolute_mean(self) -> float:
        present = [f.score for f in self.factors.values() if not f.is_missing]
        if not present:
            return 0.0
        return sum(present) / len(present)

    def get(self, name: str) -> float:
        f = self.factors.get(name)
        return f.score if f else 0.0


@dataclass(frozen=True)
class Flag:
    code: str               # "C1".."C11"
    severity: str           # "minor" | "major"
    detail: str


class FactorScores(BaseModel):
    model_config = ConfigDict(extra="ignore")
    f1_hwa: float = 0.0
    f2_rmg: float = 0.0
    f3_ext: float = 0.0
    f4_trg: float = 0.0
    f5_xctx: float = 0.0
    f6_theme: float = 0.0
    f7_macro: float = 0.0
    absolute_mean: float = 0.0        # score post-decay (conviction + ranking)
    absolute_mean_raw: float = 0.0    # score pré-decay (audit uniquement)
    decay_factor: float = 1.0         # multiplicateur appliqué [DECAY_FLOOR, 1.0]
    decay_source: str = "age"
    quantile: float = 0.0
    missing: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)


class FlagModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    severity: str
    detail: str


class SetupV4(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str
    direction: Direction
    scenario_hint: str = ""
    entry: float = 0.0
    entry_type: str = "Market"
    sl: float = 0.0
    sl_atr_multiple: float = 0.0
    tp1: float = 0.0
    tp1_atr_multiple: Optional[float] = None
    tp2: Optional[float] = None
    tp2_atr_multiple: Optional[float] = None
    rr: float = 0.0
    rr_synthetic: bool = False
    # Granularité TP1/TP2 pour le badge SR. None = ancien schéma -> legacy.
    tp1_synthetic: Optional[bool] = None
    tp2_synthetic: Optional[bool] = None
    atr_effective: float = 0.0
    atr_source: str = "unknown"
    distance_atr: float = 0.0
    choch_score: Optional[float] = None
    choch_info: Optional[str] = None   # ex: "H4 Bearish 85 (3c)"
    gps_quality: Optional[str] = None
    mtf_pct: int = 0
    rsi_h4: Optional[float] = None
    rsi_h4_status: Optional[str] = None
    age_d1: int = 0
    cal_status: CalStatus = CalStatus.OK
    cal_note: str = ""
    htf_aligned: bool = False
    sl_detail: str = ""
    rr_detail: str = ""
    rationale: str = ""
    conviction: Conviction = Conviction.BBB
    factor_scores: FactorScores = Field(default_factory=FactorScores)
    flags: list[FlagModel] = Field(default_factory=list)
    cluster: str = ""
    capped_reason: Optional[str] = None
    reject_code: Optional[str] = None
    reject_detail: Optional[str] = None
    current_price: float = 0.0          # snapshot pour preflight PRICE_PAST_TP
    asset_class: str = "forex"          # propagé depuis CanonicalAsset
    age_known: bool = True              # P1-A
    horizon_days: Optional[float] = None            # P0-B
    horizon_event: Optional[str] = None
    horizon_event_days: Optional[float] = None
    invalidation: dict[str, str] = Field(default_factory=dict)   # P1-D
    rr_if_market: Optional[float] = None            # P2-C


class Universe(BaseModel):
    model_config = ConfigDict(extra="ignore")
    passed: list[CanonicalAsset] = Field(default_factory=list)
    rejected: list[tuple[CanonicalAsset, GateCode, str]] = Field(default_factory=list)


class Eliminated(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str
    direction: Direction = Direction.NEUTRAL
    scenario: Optional[str] = None
    reject_code: str
    reject_detail: str
    rsi_h4: Optional[float] = None
    age_d1: int = 0
    cal_status: CalStatus = CalStatus.OK
    rr: Optional[float] = None
    age_known: bool = True
    asset_class: str = "forex"


@dataclass
class MarketThemes:
    strong: dict[str, str] = field(default_factory=dict)        # ccy -> "Bullish"/"Bearish"
    cohesion: dict[str, float] = field(default_factory=dict)    # ccy -> [0,1]

    def bonus_for(self, base: str, quote: Optional[str], direction: Direction) -> float:
        """F6 score in [0,1]: how well the trade rides dominant currency themes."""
        d = direction.value
        inv = "Bearish" if d == "Bullish" else "Bullish"
        contributions: list[float] = []
        if base in self.strong:
            coh = self.cohesion.get(base, 0.0)
            contributions.append(coh if self.strong[base] == d else -coh)
        if quote and quote in self.strong:
            coh = self.cohesion.get(quote, 0.0)
            contributions.append(coh if self.strong[quote] == inv else -coh)
        if not contributions:
            return 0.5  # neutral when no theme touches the pair
        signed = sum(contributions) / len(contributions)  # [-1,1]
        return _clamp01((signed + 1.0) / 2.0)

    def is_counter_theme(self, base: str, quote: Optional[str], direction: Direction) -> tuple[bool, float]:
        """True + cohesion if the trade fights a high-cohesion dominant theme."""
        d = direction.value
        inv = "Bearish" if d == "Bullish" else "Bullish"
        worst = 0.0
        counter = False
        if base in self.strong and self.strong[base] != d:
            counter = True
            worst = max(worst, self.cohesion.get(base, 0.0))
        if quote and quote in self.strong and self.strong[quote] != inv:
            counter = True
            worst = max(worst, self.cohesion.get(quote, 0.0))
        return counter, worst


# ════════════════════════════════════════════════════════════════════════════
# SECTION 6 — CONFIG
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class V4Config:
    # universe
    MIN_QUALITY: frozenset = frozenset({"A+", "A"})
    MIN_CONSENSUS_PCT: int = 50
    # F1 HWA — ordinal seniority weights
    HWA_WEIGHTS: Mapping[str, int] = field(default_factory=lambda: MappingProxyType(
        {"MN": 6, "W1": 5, "D1": 4, "H4": 3, "H1": 2, "M15": 1}))
    # F2 RMG
    RMG_FAST: tuple = ("M15", "H1")
    RMG_SLOW: tuple = ("D1", "W1")
    RMG_SCALE: float = 15.0
    RMG_MIN_TF: int = 3
    # F3 EXT
    EXT_TF_COUNT: int = 5
    # F4 TRG
    TRG_SCORE_CAP: float = 85.0
    TRG_FRESH_MAX: int = 6
    TRG_DIST_ATR_MAX: float = 1.0
    # SR structure bonus dans F4
    SR_BONUS_MAX: float = 0.20
    SR_DIST_MAX_PCT: float = 2.0
    SR_W_W1: float = 0.50
    SR_W_D1: float = 0.30
    SR_W_H4: float = 0.20
    # F6 THEME
    THEME_MIN_VOTES: int = 3
    THEME_BULL_HI: float = 0.8
    THEME_BULL_LO: float = 0.2
    THEME_COHESION_C5: float = 0.8
    # F7 MACRO
    MACRO_TAU_HOURS: float = 48.0
    # conviction (ABSOLUTE thresholds)
    AAA_MIN: float = 0.80
    AA_MIN: float = 0.68
    A_MIN: float = 0.55
    BBB_MIN: float = 0.42
    BB_MIN: float = 0.30
    MACRO_CAP_RISK_THRESHOLD: float = 0.50   # macro RISK >= 0.5 -> cap AA
    # [OPT-IN — DÉFAUT = COMPORTEMENT ACTUEL, BIT POUR BIT]
    # False : un flux tronqué neutralise f7 pour TOUS les actifs (cap AA
    #         universel — le flag est structurellement toujours vrai sur un
    #         flux hebdomadaire).
    # True  : f7 reste MESURÉ si le prochain event S/A tombe DANS la fenêtre
    #         couverte ; fail-closed conservé si le silence est invérifiable.
    # MODIFIE LES CONVICTIONS. Exige un A/B sur >= 20 sessions archivées.
    MACRO_COVERAGE_GRANULAR: bool = False
    # alpha decay : tau de exp(-age/tau). Demi-vie réelle = tau×ln(2) ≈ 24 j.
    DECAY_TIME_CONSTANT: int = 35
    DECAY_FLOOR: float = 0.30        # un signal très vieux ne score jamais 0
    # contradictions
    C1_TRG_MIN: float = 0.5
    C1_RMG_MAX: float = 0.35
    C2_EXT_MAX: float = 0.3
    C2_HWA_MAX: float = 0.5
    C4_DIST_ATR: float = 1.0
    # P0-B — cohérence d'horizon (C7). [CALIBRATION REQUISE] ordre de
    # grandeur, pas une mesure ; HORIZON_MARGIN absorbe l'imprécision.
    HORIZON_ATR_REALIZATION_RATE: float = 0.6
    HORIZON_MARGIN: float = 1.25
    # P1-B — réconciliation de fraîcheur (C9)
    FRESHNESS_TOLERANCE_BARS: int = 1
    FRESHNESS_NEUTRAL: float = 0.5
    # P1-C — régime macro portefeuille
    MACRO_REGIME_WINDOW_H: float = 48.0   # fenêtre GLISSANTE
    MACRO_REGIME_MIN_S: int = 3           # slots tier-S DISTINCTS
    MACRO_VACUUM_H: float = 120.0
    # C10 — divergence RSI senior contre-tendance. [CALIBRATION REQUISE]
    C10_DIV_SENIOR_TFS: tuple = ("W1", "D1")
    C10_DIV_MIN_EVIDENCE: float = 0.25
    # P1-D
    INVALIDATION_TIME_MULT: float = 1.5
    # preflight
    RR_MIN: float = 1.5
    RR_MAX: float = 20.0
    SL_FLOOR_MULT: float = 0.8
    SL_MAX_ATR_MULT: float = 3.0   # P2-B
    DEFAULT_BB_MULT: float = 1.5
    BB_REGIME_MULT: Mapping[str, float] = field(default_factory=lambda: MappingProxyType(
        {"Squeeze": 1.0, "Normal": 1.5, "Expansion": 2.0}))
    FRESH_ATR_MAX: float = 0.3
    LIMIT_ZONE_MAX_DIST: float = 2.0
    TP1_ATR_MULT: float = 2.0
    TP2_ATR_MULT: float = 1.0
    TP_MAX_ATR_MULT: float = 4.0       # zone SR utilisée comme TP ≤ 4×ATR
    # selection
    MAX_SETUPS: int = 5
    MAX_EXPOSURE_PER_CCY: int = 2
    MIN_CONVICTION: str = "BB"       # conviction minimum post-decay en sélection

    @classmethod
    def from_dict(cls, d: dict) -> "V4Config":
        base = cls()
        kw = {}
        for k, v in (d or {}).items():
            if hasattr(base, k):
                kw[k] = v
        return cls(**kw)


CONFIG = V4Config()

# Execution-context point maps for F5 XCTX (bounded, categorical)
_XCTX_FORCE = MappingProxyType({
    "fort": 1.0, "strong": 1.0,
    "moyen": 0.6, "medium": 0.6,
    "": 0.5,
    "faible": 0.0, "weak": 0.0,
})
_XCTX_VOL = MappingProxyType({
    "haute": 1.0, "high": 1.0,
    "moyenne": 0.55, "medium": 0.55,
    "": 0.5,
    "faible": 0.3, "basse": 0.3, "low": 0.3,
})
_XCTX_SESSION = MappingProxyType({
    "london": 1.0, "newyork": 1.0, "ny": 1.0, "us": 1.0, "london_ny_overlap": 1.0,
    "asian": 0.5, "tokyo": 0.5, "sydney": 0.5,
    "off": 0.0,
    "": 0.3,
})
_XCTX_BB = MappingProxyType({"squeeze": 1.0, "normal": 0.6, "expansion": 0.3, "": 0.6})

_EXT_STATUSES = ("extreme_overbought", "extreme_oversold", "overbought", "oversold")

# RSI divergence — poids par timeframe (somme théorique max = 0.74).
# Cap final à 0.40 sur le score F2.
_DIV_TF_WEIGHT: Mapping[str, float] = MappingProxyType(
    {"W1": 0.35, "D1": 0.20, "H4": 0.12, "H1": 0.05, "M15": 0.02}
)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 7 — THEME DETECTION
# ════════════════════════════════════════════════════════════════════════════
def detect_currency_themes(assets: Mapping[str, CanonicalAsset], cfg: V4Config = CONFIG) -> MarketThemes:
    votes: dict[str, list[str]] = defaultdict(list)
    for a in assets.values():
        if not a.mtf or a.mtf.direction is Direction.NEUTRAL:
            continue
        d = a.mtf.direction.value
        inv = "Bearish" if d == "Bullish" else "Bullish"
        votes[a.base].append(d)
        if a.quote:
            votes[a.quote].append(inv)
    strong: dict[str, str] = {}
    cohesion: dict[str, float] = {}
    for ccy, vs in votes.items():
        if len(vs) < cfg.THEME_MIN_VOTES:
            continue
        bull = vs.count("Bullish") / len(vs)
        if bull >= cfg.THEME_BULL_HI:
            strong[ccy] = "Bullish"
            cohesion[ccy] = bull
        elif bull <= cfg.THEME_BULL_LO:
            strong[ccy] = "Bearish"
            cohesion[ccy] = 1.0 - bull
    return MarketThemes(strong=strong, cohesion=cohesion)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 7b — RÉGIME MACRO PORTEFEUILLE (P1-C)
# ════════════════════════════════════════════════════════════════════════════
def classify_macro_regime(cal: Optional[CalendarSets], clock: "Clock",
                          cfg: V4Config = CONFIG) -> MacroRegime:
    """Régime calendaire au niveau PORTEFEUILLE. Déterministe : comptage seul.
    Dédoublonnage par (devise, datetime) — FF publie plusieurs lignes pour un
    même communiqué. Fenêtre GLISSANTE de MACRO_REGIME_WINDOW_H."""
    if cal is None:
        return MacroRegime.UNKNOWN
    now = clock.now_utc
    horizon = list(cal.blackout) + list(cal.proximity) + list(cal.watch)

    s_slots: set[tuple[str, datetime]] = set()
    sa_future: list[float] = []
    sa_past: list[tuple[float, CalendarEvent]] = []
    for ev in horizon:
        delta = (ev.datetime_utc - now).total_seconds() / 3600.0
        if ev.tier is EventTier.S and delta >= 0:
            s_slots.add((ev.currency, ev.datetime_utc))
        if ev.tier in (EventTier.S, EventTier.A):
            (sa_future.append(delta) if delta >= 0 else sa_past.append((delta, ev)))

    times = sorted(t for _, t in s_slots)
    for i, t0 in enumerate(times):
        cnt = sum(1 for t in times[i:]
                  if (t - t0).total_seconds() / 3600.0 <= cfg.MACRO_REGIME_WINDOW_H)
        if cnt >= cfg.MACRO_REGIME_MIN_S:
            return MacroRegime.PRE_POLICY_COMPRESSION

    for delta, ev in sa_past:
        _, after = TIER_WINDOWS.get(ev.tier, DEFAULT_TIER_WINDOW)
        if delta >= -after:
            return MacroRegime.POST_POLICY_REPRICING

    if not any(0.0 <= d <= cfg.MACRO_VACUUM_H for d in sa_future):
        return MacroRegime.EVENT_VACUUM
    return MacroRegime.EVENT_DRIFT


# ════════════════════════════════════════════════════════════════════════════
# SECTION 8 — FACTORS F1..F7  (pure functions, all bounded [0,1])
# ════════════════════════════════════════════════════════════════════════════
def _rsi_value(a: CanonicalAsset, tf: str) -> Optional[float]:
    d = a.rsi_by_tf.get(tf) or a.rsi_by_tf.get(tf.upper()) or a.rsi_by_tf.get(tf.lower())
    if isinstance(d, dict):
        return _safe_float(d.get("value"))
    return _safe_float(d)


def _rsi_status(a: CanonicalAsset, tf: str) -> str:
    d = a.rsi_by_tf.get(tf) or a.rsi_by_tf.get(tf.upper()) or a.rsi_by_tf.get(tf.lower())
    if isinstance(d, dict):
        return str(d.get("status") or "").lower()
    return ""


def _divergence_penalty(a: CanonicalAsset) -> float:
    """Pénalité [0.0, 0.40] : divergences RSI confirmées CONTRAIRES au trade.
    Seules les entrées div_confirmed=True comptent ; poids = strength ×
    confidence, pondéré par TF. Capé à 0.40. 0.0 si MTF absent."""
    if a.mtf is None:
        return 0.0
    direction = a.mtf.direction
    penalty = 0.0
    for tf, w in _DIV_TF_WEIGHT.items():
        d = a.rsi_by_tf.get(tf) or a.rsi_by_tf.get(tf.upper()) or a.rsi_by_tf.get(tf.lower())
        if not isinstance(d, dict):
            continue
        if not d.get("div_confirmed"):
            continue
        div_dir = str(d.get("divergence") or "").lower()
        is_contra = (
            (direction is Direction.BULLISH and div_dir == "bearish")
            or (direction is Direction.BEARISH and div_dir == "bullish")
        )
        if not is_contra:
            continue
        strength = _safe_float(d.get("div_strength_score")) or 0.0
        confidence = _safe_float(d.get("div_confidence_score")) or 0.0
        penalty += w * strength * confidence
    return min(penalty, 0.40)


def _aligned_trigger(a: CanonicalAsset) -> Optional[StructureEventView]:
    """Most recent Fresh CHoCH aligned with MTF direction (lowest candles_elapsed)."""
    if a.mtf is None:
        return None
    want = a.mtf.direction
    cands = [ev for ev in a.structure_events
             if ev.status.lower() == "fresh" and _dir_eq(ev.direction, want)]
    if not cands:
        return None
    return min(cands, key=lambda e: e.candles_elapsed)


def audit_freshness(ev: StructureEventView, now: Optional[datetime],
                    cfg: V4Config = CONFIG) -> tuple[FreshnessAudit, int]:
    """P1-B. signal_time absent ou TF non supporté -> UNKNOWN, comportement
    STRICTEMENT INCHANGÉ (mode silencieux sur feed sans le champ)."""
    if now is None or ev.signal_time is None:
        return FreshnessAudit.UNKNOWN, -1
    bars = _elapsed_bars_fx(ev.signal_time, now, ev.timeframe)
    if bars < 0:
        return FreshnessAudit.UNKNOWN, -1
    if abs(bars - int(ev.candles_elapsed)) <= cfg.FRESHNESS_TOLERANCE_BARS:
        return FreshnessAudit.FRESH, bars
    return FreshnessAudit.STALE, bars


def f1_hwa(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:
    if a.mtf is None:
        return ScoredFactor("f1_hwa", None, 0.5, True, "MTF absent")
    biases = a.mtf.biases or {}
    direction = a.mtf.direction
    num = 0
    den = 0
    conflicts: list[str] = []
    for tf, w in cfg.HWA_WEIGHTS.items():
        b = (biases.get(tf) or "Range")
        if _dir_eq(_norm_dir(b), direction) and _norm_dir(b) is not Direction.NEUTRAL:
            s = 1
        elif _norm_dir(b) is _opposite_dir(direction) and _opposite_dir(direction) is not Direction.NEUTRAL:
            s = -1
            conflicts.append(tf)
        else:
            s = 0
        num += w * s
        den += w
    raw = (num / den) if den else 0.0
    score = _clamp01((raw + 1.0) / 2.0)
    detail = f"HWA num={num}/den={den} raw={raw:.2f} conflits={conflicts or '∅'}"
    return ScoredFactor("f1_hwa", raw, score, a.mtf is None, detail)


def f2_rmg(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:
    if a.mtf is None:
        return ScoredFactor("f2_rmg", None, 0.5, True, "MTF absent")
    fast_vals = [v for v in (_rsi_value(a, tf) for tf in cfg.RMG_FAST) if v is not None]
    slow_vals = [v for v in (_rsi_value(a, tf) for tf in cfg.RMG_SLOW) if v is not None]
    n_available = len(fast_vals) + len(slow_vals)
    if not fast_vals or not slow_vals or n_available < cfg.RMG_MIN_TF:
        return ScoredFactor("f2_rmg", None, 0.5, True,
                            f"RSI insuffisant ({n_available} TF)")
    fast = sum(fast_vals) / len(fast_vals)
    slow = sum(slow_vals) / len(slow_vals)
    grad = fast - slow
    signed = grad if a.mtf.direction is Direction.BULLISH else -grad
    score = _clamp01(0.5 + 0.5 * math.tanh(signed / cfg.RMG_SCALE))
    div_penalty = _divergence_penalty(a)
    score = _clamp01(score - div_penalty)
    detail = f"RMG fast={fast:.1f} slow={slow:.1f} grad={grad:.1f} signed={signed:.1f}"
    if div_penalty > 0.0:
        detail += f" | div_penalty={div_penalty:.3f}"
    return ScoredFactor("f2_rmg", grad, score, False, detail)


def f3_ext(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:
    if a.mtf is None:
        return ScoredFactor("f3_ext", None, 0.5, True, "MTF absent")
    direction = a.mtf.direction
    ext_in_dir = 0
    checked = 0
    for tf in cfg.HWA_WEIGHTS.keys():
        st = _rsi_status(a, tf)
        if not st:
            continue
        checked += 1
        is_ext = any(k in st for k in _EXT_STATUSES)
        if not is_ext:
            continue
        if direction is Direction.BULLISH and ("overbought" in st):
            ext_in_dir += 1
        elif direction is Direction.BEARISH and ("oversold" in st):
            ext_in_dir += 1
    score = _clamp01(1.0 - ext_in_dir / cfg.EXT_TF_COUNT)
    detail = f"{ext_in_dir}/{cfg.EXT_TF_COUNT} TF surchauffés dans le sens (checked={checked})"
    return ScoredFactor("f3_ext", float(ext_in_dir), score, False, detail)


def _sr_structure_bonus(a: CanonicalAsset, cfg: V4Config) -> tuple[float, str]:
    """Bonus SR [0.0, SR_BONUS_MAX] si une zone SR proche (≤ SR_DIST_MAX_PCT)
    et de side compatible est confirmée sur plusieurs TF (composite W1/D1/H4)."""
    if a.mtf is None or not a.zones:
        return 0.0, "SR: pas de zones"

    direction = a.mtf.direction

    def _side_ok(z: ZoneView) -> bool:
        s = z.side.upper()
        if direction is Direction.BULLISH:
            return s in ("BUY", "SUPPORT", "ROLE REVERSE", "")
        return s in ("SELL", "RESISTANCE", "ROLE REVERSE", "")

    candidates = [z for z in a.zones
                  if z.distance_pct <= cfg.SR_DIST_MAX_PCT and _side_ok(z)]
    if not candidates:
        return 0.0, f"SR: aucune zone compatible <{cfg.SR_DIST_MAX_PCT}%"

    best = min(candidates, key=lambda z: z.distance_pct)
    composite = (
        cfg.SR_W_W1 * float(best.has_weekly)
        + cfg.SR_W_D1 * float(best.has_daily)
        + cfg.SR_W_H4 * float(best.has_h4)
    )
    bonus = _clamp01(composite) * cfg.SR_BONUS_MAX
    tfs = best.timeframes or (
        (["W1"] if best.has_weekly else [])
        + (["D1"] if best.has_daily else [])
        + (["H4"] if best.has_h4 else [])
    )
    detail = (f"SR: zone@{best.level:.5f} dist={best.distance_pct:.2f}% "
              f"TF={tfs} composite={composite:.2f} bonus={bonus:.3f}")
    return bonus, detail


def f4_trg(a: CanonicalAsset, cfg: V4Config = CONFIG, *,
           now: Optional[datetime] = None) -> ScoredFactor:
    ev = _aligned_trigger(a)
    if ev is None:
        return ScoredFactor("f4_trg", None, 0.0, True, "pas de trigger aligné")
    score_n = min(ev.confluence_score, cfg.TRG_SCORE_CAP) / cfg.TRG_SCORE_CAP

    status, bars_real = audit_freshness(ev, now, cfg)      # P1-B
    if status is FreshnessAudit.STALE:
        fresh = cfg.FRESHNESS_NEUTRAL
        fresh_note = (f"fresh NEUTRALISÉ ({cfg.FRESHNESS_NEUTRAL:.2f}) — "
                      f"déclaré {ev.candles_elapsed}c, réel {bars_real}c")
    else:
        fresh = 1.0 - min(ev.candles_elapsed, cfg.TRG_FRESH_MAX) / cfg.TRG_FRESH_MAX
        fresh_note = (f"fresh={fresh:.2f} [{status.value}"
                      + (f", réel {bars_real}c" if bars_real >= 0 else "") + "]")

    dist = ev.distance_atr_multiple if ev.distance_atr_multiple is not None else cfg.TRG_DIST_ATR_MAX
    proximity = 1.0 - min(dist, cfg.TRG_DIST_ATR_MAX) / cfg.TRG_DIST_ATR_MAX
    base = _clamp01(0.4 * score_n + 0.3 * fresh + 0.3 * proximity)
    sr_bonus, sr_detail = _sr_structure_bonus(a, cfg)
    score = _clamp01(base + sr_bonus)
    detail = (f"TRG score_n={score_n:.2f} {fresh_note} prox={proximity:.2f} "
              f"(conf={ev.confluence_score:.0f}, {ev.candles_elapsed}c, {dist:.2f}ATR) "
              f"| {sr_detail}")
    return ScoredFactor("f4_trg", ev.confluence_score, score, False, detail)


def _norm_lookup(v: Any) -> str:
    return str(v or "").strip().lower()


def f5_xctx(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:  # noqa: ARG001
    ev = _aligned_trigger(a)
    if ev is None:
        return ScoredFactor("f5_xctx", None, 0.5, True, "trigger absent (contexte neutre)")
    force = _XCTX_FORCE.get(_norm_lookup(ev.force), 0.5)
    vol = _XCTX_VOL.get(_norm_lookup(ev.volatility), 0.5)
    session = _XCTX_SESSION.get(_norm_lookup(ev.session), 0.3)
    bb = _XCTX_BB.get((ev.bb_regime or "").lower(), 0.6)
    score = _clamp01((force + vol + session + bb) / 4.0)
    detail = (f"XCTX force={force:.1f} vol={vol:.1f} sess={session:.1f} bb={bb:.1f} "
              f"({ev.force}/{ev.volatility}/{ev.session}/{ev.bb_regime})")
    return ScoredFactor("f5_xctx", None, score, False, detail)


def f6_theme(a: CanonicalAsset, themes: MarketThemes, cfg: V4Config = CONFIG) -> ScoredFactor:  # noqa: ARG001
    if a.mtf is None:
        return ScoredFactor("f6_theme", None, 0.5, True, "MTF absent")
    score = themes.bonus_for(a.base, a.quote, a.mtf.direction)
    detail = (f"THEME {a.base}/{a.quote or '—'} dir={a.mtf.direction.value} "
              f"strong={themes.strong} -> {score:.2f}")
    return ScoredFactor("f6_theme", None, score, False, detail)


def _parse_ff_value(s: Optional[str]) -> Optional[float]:
    """Parse les valeurs Forex Factory ('0.5%', '25.8K', '-0.1%') en float.
    None si absent ou non numérique (ex: Rate Statement)."""
    if not s or s in ("—", "", "N/A", "n/a"):
        return None
    try:
        s2 = (s.strip()
               .replace("%", "")
               .replace("K", "e3")
               .replace("B", "e9")
               .replace("M", "e6"))
        return float(s2)
    except ValueError:
        return None


def _surprise_factor(ev: CalendarEvent) -> float:
    """Facteur [0.0, 1.0] de magnitude de surprise pour un event passé.
    1.0 = inline/non mesurable ; 0.0 = surprise majeure. Events sans chiffre
    (Press Conference…) reçoivent 0.7."""
    actual = _parse_ff_value(ev.actual)
    forecast = _parse_ff_value(ev.forecast)
    if actual is None or forecast is None:
        return 0.7
    if abs(forecast) < 1e-9:
        return 0.5
    deviation = abs(actual - forecast) / (abs(forecast) + 1e-9)
    return _clamp01(1.0 - min(deviation * 2.0, 0.8))


def f7_macro(a: CanonicalAsset, cal: Optional[CalendarSets], clock: Clock,
             cfg: V4Config = CONFIG) -> ScoredFactor:
    # cal absent OU flux tronqué (mode non granulaire) -> FAIL-CLOSED :
    # risque NON écarté (score 0.0), pas risque nul.
    if cal is None or (not cfg.MACRO_COVERAGE_GRANULAR and cal.feed_horizon_truncated):
        return ScoredFactor("f7_macro", None, 0.0, True,
                            "calendrier absent ou tronqué — fail-closed (risque NON écarté, "
                            "pas risque nul)")
    sides = {a.base, (a.quote or "")}
    if sides & cal.suspended_ccy:
        return ScoredFactor("f7_macro", 1.0, 0.0, False, "BLACKOUT actif")
    now = clock.now_utc
    horizon: list[CalendarEvent] = list(cal.blackout) + list(cal.proximity) + list(cal.watch)

    # ── Chemin futur ────────────────────────────────────────────────────────
    relevant_h: list[float] = []
    for ev in horizon:
        if ev.tier not in (EventTier.S, EventTier.A):
            continue
        if ev.currency not in sides:
            continue
        delta = (ev.datetime_utc - now).total_seconds() / 3600.0
        if delta >= 0:
            relevant_h.append(delta)

    if not relevant_h:
        if cfg.MACRO_COVERAGE_GRANULAR:
            cov = set(cal.covered_currencies or ())
            uncovered = sorted(c for c in sides
                               if cov and c in _DESK_CURRENCIES and c not in cov)
            if uncovered:
                return ScoredFactor("f7_macro", None, 0.0, True,
                                    f"devise(s) hors couverture du flux "
                                    f"({', '.join(uncovered)}) — fail-closed "
                                    f"(silence invérifiable)")
            if cal.feed_horizon_truncated:
                return ScoredFactor("f7_macro", None, 0.0, True,
                                    "aucun event S/A dans la fenêtre couverte mais flux "
                                    "tronqué — fail-closed (silence invérifiable)")
        base_score = 1.0
        base_detail = "aucun event S/A futur"
        base_risk = 0.0
    else:
        hours = min(relevant_h)
        base_risk = math.exp(-hours / cfg.MACRO_TAU_HOURS)
        base_score = _clamp01(1.0 - base_risk)
        base_detail = f"MACRO event S/A dans {hours:.1f}h risk={base_risk:.2f} -> {base_score:.2f}"

    # ── R3 — Risque résiduel post-event (events passés récents) ────────────
    # Cap absolu 0.25 : jamais dominant, ne peut pas inverser un signal.
    residual_parts: list[str] = []
    residual_penalty = 0.0
    for ev in horizon:
        if ev.tier not in (EventTier.S, EventTier.A):
            continue
        if ev.currency not in sides:
            continue
        delta = (ev.datetime_utc - now).total_seconds() / 3600.0
        if delta >= 0:
            continue   # futur, déjà traité ci-dessus
        _, after = TIER_WINDOWS.get(ev.tier, (2.0, 24.0))
        if delta < -after:
            continue   # hors fenêtre post-event
        surprise = _surprise_factor(ev)
        recency = math.exp(delta / max(after / 3.0, 1.0))   # delta < 0 → recency ∈ (0,1)
        penalty = recency * (1.0 - surprise)
        if penalty > 0.05:
            residual_penalty += penalty
            residual_parts.append(
                f"résidu {ev.currency} {ev.event_name[:18]} surprise={surprise:.2f}"
            )

    residual_penalty = min(residual_penalty, 0.25)
    final_score = _clamp01(base_score - residual_penalty)
    detail_parts = [base_detail]
    if residual_parts:
        detail_parts.append("post-event: " + "; ".join(residual_parts))
    detail = " | ".join(detail_parts)

    return ScoredFactor("f7_macro", base_risk if relevant_h else 0.0, final_score, False, detail)


def build_factor_vector(a: CanonicalAsset, themes: MarketThemes,
                        cal: Optional[CalendarSets], clock: Clock,
                        cfg: V4Config = CONFIG) -> FactorVector:
    factors = {
        "f1_hwa": f1_hwa(a, cfg),
        "f2_rmg": f2_rmg(a, cfg),
        "f3_ext": f3_ext(a, cfg),
        "f4_trg": f4_trg(a, cfg, now=clock.now_utc),
        "f5_xctx": f5_xctx(a, cfg),
        "f6_theme": f6_theme(a, themes, cfg),
        "f7_macro": f7_macro(a, cal, clock, cfg),
    }
    return FactorVector(symbol=a.symbol, factors=factors)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9 — SCORING (absolute) + CROSS-SECTION (tie-break/diversif ONLY)
# ════════════════════════════════════════════════════════════════════════════
def _alpha_decay_factor(age_d1: int, cfg: V4Config) -> float:
    """Multiplicateur [DECAY_FLOOR, 1.0] : max(DECAY_FLOOR, exp(-age/tau)).
    tau = DECAY_TIME_CONSTANT = 35 → demi-vie réelle ≈ 24,3 j.
    age=0j -> 1.000 · age=24j -> ≈0.505 · age=35j -> ≈0.368 · age=50j -> floor 0.30."""
    if age_d1 <= 0:
        return 1.0
    raw = math.exp(-age_d1 / cfg.DECAY_TIME_CONSTANT)
    return max(cfg.DECAY_FLOOR, raw)


def resolve_unknown_decay(known_ages: list[int], cfg: V4Config) -> tuple[float, str]:
    """P1-A — decay quand age_d1 est None : médiane empirique du decay de
    l'univers du run (déterministe, aucune constante en dur). Repli sur
    DECAY_FLOOR si l'univers ne fournit aucune référence. Évite d'attribuer
    le bonus de fraîcheur maximal à une absence d'information."""
    if known_ages:
        return _median([_alpha_decay_factor(a, cfg) for a in known_ages]), "universe_median"
    return cfg.DECAY_FLOOR, "floor_no_reference"


def compute_quantiles(vectors: list[FactorVector]) -> dict[str, float]:
    """Pure-python percentile rank of absolute_mean within the universe."""
    means = [(v.symbol, v.absolute_mean) for v in vectors]
    if not means:
        return {}
    values = sorted(m for _, m in means)
    n = len(values)
    out: dict[str, float] = {}
    for sym, m in means:
        below = sum(1 for x in values if x < m)
        equal = sum(1 for x in values if x == m)
        out[sym] = (below + 0.5 * equal) / n if n else 0.0
    return out


def rank_setups(setups: list[SetupV4], cfg: V4Config = CONFIG) -> list[SetupV4]:  # noqa: ARG001
    """Sort DESC by absolute_mean; tie-break f4 -> f1 -> low-macro-risk -> quantile.
    Quantile NEVER influences conviction; only ordering here. Symbol = clé
    stable finale pour reproductibilité bit-pour-bit."""
    def key(s: SetupV4):
        fs = s.factor_scores
        return (
            -fs.absolute_mean,
            -fs.f4_trg,
            -fs.f1_hwa,
            -fs.f7_macro,      # higher f7 = lower macro risk preferred
            -fs.quantile,
            s.symbol,
        )
    return sorted(setups, key=key)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9b — HORIZON DE CIBLE ET PROCHAIN ÉVÉNEMENT MACRO (P0-B)
# ════════════════════════════════════════════════════════════════════════════
def _target_horizon_days(a: CanonicalAsset, lv: "LevelBundle",
                         cfg: V4Config = CONFIG) -> Optional[float]:
    """[ORDRE DE GRANDEUR] HORIZON_ATR_REALIZATION_RATE n'est pas calibré."""
    atr_d = (a.mtf.atr_daily if a.mtf else None) or lv.atr_effective
    if not atr_d or atr_d <= 0:
        return None
    denom = cfg.HORIZON_ATR_REALIZATION_RATE * atr_d
    return abs(lv.tp1 - lv.entry) / denom if denom > 0 else None


def _next_macro_event_days(a: CanonicalAsset, cal: Optional[CalendarSets],
                           clock: Clock) -> tuple[Optional[float], Optional[str]]:
    """Prochain event tier S/A touchant base ou quote. (jours, libellé)."""
    if cal is None:
        return None, None
    sides = {a.base, (a.quote or "")}
    now = clock.now_utc
    best: Optional[tuple[float, CalendarEvent]] = None
    for ev in list(cal.blackout) + list(cal.proximity):
        if ev.tier not in (EventTier.S, EventTier.A) or ev.currency not in sides:
            continue
        d = (ev.datetime_utc - now).total_seconds() / 3600.0
        if d < 0:
            continue
        if best is None or d < best[0]:
            best = (d, ev)
    if best is None:
        return None, None
    days = best[0] / 24.0
    return days, f"{best[1].currency} {best[1].event_name} (J+{days:.1f})"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 10 — CONTRADICTIONS C1..C11
# ════════════════════════════════════════════════════════════════════════════
def _c1_struct_vs_momentum(fv: FactorVector, cfg: V4Config) -> Optional[Flag]:
    if fv.get("f4_trg") > cfg.C1_TRG_MIN and fv.get("f2_rmg") < cfg.C1_RMG_MAX:
        return Flag("C1", "minor",
                    f"Structure forte (TRG={fv.get('f4_trg'):.2f}) mais momentum faible "
                    f"(RMG={fv.get('f2_rmg'):.2f})")
    return None


def _c2_momentum_vs_trend(fv: FactorVector, cfg: V4Config) -> Optional[Flag]:
    if fv.get("f3_ext") < cfg.C2_EXT_MAX and fv.get("f1_hwa") < cfg.C2_HWA_MAX:
        return Flag("C2", "major",
                    f"Parabolique : surchauffe (EXT={fv.get('f3_ext'):.2f}) + alignement "
                    f"faible (HWA={fv.get('f1_hwa'):.2f})")
    return None


def _c3_trend_vs_calendar(a: CanonicalAsset, fv: FactorVector,
                          cal: Optional[CalendarSets], cfg: V4Config) -> Optional[Flag]:
    if fv.get("f7_macro") >= cfg.MACRO_CAP_RISK_THRESHOLD:
        return None
    if cal is None:
        return None
    sides = {a.base, (a.quote or "")}
    # Scope S+A : exactement les mêmes events qui font baisser f7_macro.
    tier_sa = [e for e in (list(cal.blackout) + list(cal.proximity))
               if e.tier in (EventTier.S, EventTier.A) and e.currency in sides]
    if tier_sa:
        names = ", ".join(f"{e.currency} {e.event_name}" for e in tier_sa[:2])
        return Flag("C3", "major",
                    f"Risque calendaire élevé (MACRO={fv.get('f7_macro'):.2f}) : {names}")
    return None


def _c4_quality_vs_potential(a: CanonicalAsset, cfg: V4Config) -> Optional[Flag]:
    ev = _aligned_trigger(a)
    if ev is None or ev.distance_atr_multiple is None:
        return None
    if ev.distance_atr_multiple > cfg.C4_DIST_ATR:
        return Flag("C4", "minor",
                    f"Chasing : prix à {ev.distance_atr_multiple:.2f}×ATR du trigger "
                    f"(> {cfg.C4_DIST_ATR})")
    return None


def _c5_trade_vs_theme(a: CanonicalAsset, themes: MarketThemes, cfg: V4Config) -> Optional[Flag]:
    if a.mtf is None:
        return None
    counter, coh = themes.is_counter_theme(a.base, a.quote, a.mtf.direction)
    if counter and coh >= cfg.THEME_COHESION_C5:
        return Flag("C5", "major",
                    f"Trade contre thème devise dominant (cohésion={coh:.2f})")
    return None


def _c6_structural_escalation(a: CanonicalAsset) -> Optional[Flag]:
    """C6 — escalade structurelle counter-MTF multi-TF (market_context)."""
    evs = (a.market_context or {}).get("structure_events_summary") or {}
    if not evs.get("escalation_detected"):
        return None
    seq = evs.get("escalation_sequence") or []
    seq_str = " → ".join(seq) if seq else "multi-TF"
    return Flag("C6", "major",
                f"Escalade structurelle counter-MTF : {seq_str}")


def _c7_horizon_coherence(horizon: Optional[tuple], cfg: V4Config) -> Optional[Flag]:
    """P0-B — la cible est-elle atteignable avant le prochain choc macro ?"""
    if not horizon:
        return None
    t_target, t_event, label = horizon
    if t_target is None or t_event is None:
        return None
    if t_target > t_event * cfg.HORIZON_MARGIN:
        return Flag("C7", "major",
                    f"Horizon : cible ≈ {t_target:.1f} j > {label or 'event macro'} "
                    f"à {t_event:.1f} j (marge ×{cfg.HORIZON_MARGIN})")
    return None


def _c8_age_unknown(a: CanonicalAsset) -> Optional[Flag]:
    """P1-A — âge de structure inconnu : decay sans mesure."""
    if a.mtf is not None and a.mtf.age_d1 is None:
        return Flag("C8", "minor",
                    "Âge de structure inconnu — decay substitué par la médiane de l'univers")
    return None


def _c9_freshness_mismatch(a: CanonicalAsset, now: Optional[datetime],
                           cfg: V4Config) -> Optional[Flag]:
    """P1-B — candles_elapsed déclaré incohérent avec signal_time."""
    ev = _aligned_trigger(a)
    if ev is None:
        return None
    status, bars = audit_freshness(ev, now, cfg)
    if status is FreshnessAudit.STALE:
        return Flag("C9", "minor",
                    f"Fraîcheur incohérente : déclaré {ev.candles_elapsed}c, "
                    f"réel {bars}c ({ev.timeframe})")
    return None


def _c11_stale_price_market_entry(a: CanonicalAsset, entry_type: str) -> Optional[Flag]:
    """V4-01 — entrée "Market" (seul cas où compute_entry utilise
    a.current_price TEL QUEL) construite sur un prix marqué périmé par la
    couche de fusion amont -> flag major (défaut d'intégrité de donnée).
    Zéro régression : rien n'est émis si current_price_source est absent,
    "live", ou si l'entrée est "Limit"."""
    if entry_type != "Market":
        return None
    if (a.current_price_source or "").lower() != "stale":
        return None
    return Flag("C11", "major",
                "Entrée Market construite sur un prix marqué périmé "
                "(current_price_source=stale) par la couche de fusion en amont")


def _c10_htf_divergence(a: CanonicalAsset, cfg: V4Config = CONFIG) -> Optional[Flag]:
    """Divergence RSI confirmée, CONTRAIRE au trade, sur TF senior (W1/D1).
    Source de vérité = a.rsi_by_tf (identique à _divergence_penalty), PAS
    market_context. None si MTF absent/Neutral -> UNKNOWN, pas de flag inventé."""
    if a.mtf is None or a.mtf.direction is Direction.NEUTRAL:
        return None
    contra = _opposite_dir(a.mtf.direction)
    hits: list[str] = []
    for tf in cfg.C10_DIV_SENIOR_TFS:
        d = (a.rsi_by_tf.get(tf) or a.rsi_by_tf.get(tf.upper())
             or a.rsi_by_tf.get(tf.lower()))
        if not isinstance(d, dict) or not d.get("div_confirmed"):
            continue
        if _norm_dir(d.get("divergence")) is not contra:
            continue
        evidence = ((_safe_float(d.get("div_strength_score")) or 0.0)
                    * (_safe_float(d.get("div_confidence_score")) or 0.0))
        if evidence >= cfg.C10_DIV_MIN_EVIDENCE:
            hits.append(f"{tf}={evidence:.2f}")
    if not hits:
        return None
    return Flag("C10", "major",
                f"Divergence {contra.value} confirmée contre-tendance sur TF "
                f"senior ({', '.join(hits)}) — structure > alignement MTF")


def detect_contradictions(a: CanonicalAsset, fv: FactorVector, themes: MarketThemes,
                          cal: Optional[CalendarSets], cfg: V4Config = CONFIG, *,
                          now: Optional[datetime] = None,
                          horizon: Optional[tuple] = None) -> list[Flag]:
    flags: list[Flag] = []
    for f in (
        _c1_struct_vs_momentum(fv, cfg),
        _c2_momentum_vs_trend(fv, cfg),
        _c3_trend_vs_calendar(a, fv, cal, cfg),
        _c4_quality_vs_potential(a, cfg),
        _c5_trade_vs_theme(a, themes, cfg),
        _c6_structural_escalation(a),
        _c7_horizon_coherence(horizon, cfg),
        _c8_age_unknown(a),
        _c9_freshness_mismatch(a, now, cfg),
        _c10_htf_divergence(a, cfg),
    ):
        if f is not None:
            flags.append(f)
    return flags


# ════════════════════════════════════════════════════════════════════════════
# SECTION 11 — GRADE  (vetos -> caps -> grid). Quantile NEVER enters here.
# ════════════════════════════════════════════════════════════════════════════
def apply_caps(a: CanonicalAsset, fv: FactorVector, cfg: V4Config = CONFIG, *,
               flags: Optional[list[Flag]] = None,
               regime: Optional[MacroRegime] = None
               ) -> tuple[Optional[Conviction], Optional[str]]:
    """Returns the most restrictive cap and a human reason, or (None, None)."""
    caps: list[tuple[Conviction, str]] = []
    # Deux causes distinctes de cap BBB : conviction_cap=BBB (configuration)
    # et atr_source=synthetic.
    if (a.conviction_cap or "").upper() == "BBB":
        caps.append((Conviction.BBB, "conviction_cap=BBB"))
    if (a.atr_source or "").lower() == "synthetic":
        caps.append((Conviction.BBB, "ATR source synthétique"))
    # High macro risk -> AA  (risk = 1 - f7_score)
    macro_risk = 1.0 - fv.get("f7_macro")
    if macro_risk >= cfg.MACRO_CAP_RISK_THRESHOLD:
        if "f7_macro" in fv.missing:
            caps.append((Conviction.AA,
                         "risque macro NON ÉVALUÉ (couverture calendaire "
                         "insuffisante) — cap prudentiel"))
        else:
            caps.append((Conviction.AA, f"risque macro élevé ({macro_risk:.2f})"))
    if (a.market_context or {}).get("structural_risk") == "Critical":
        caps.append((Conviction.BBB, "risque structurel critique (REVERSAL_RISK)"))
    # P0-B — cap, PAS veto : la thèse peut rester valide avec une cible
    # intermédiaire redéfinie (décision d'opérateur, pas de moteur).
    if flags and any(f.code == "C7" for f in flags):
        caps.append((Conviction.BB, "horizon incohérent avec le calendrier (C7)"))
    # P1-C — régime portefeuille, aucun multiplicateur de score.
    if regime is MacroRegime.PRE_POLICY_COMPRESSION:
        caps.append((Conviction.AA,
                     f"régime pré-policy (≥{cfg.MACRO_REGIME_MIN_S} releases S / "
                     f"{cfg.MACRO_REGIME_WINDOW_H:.0f}h)"))
    if not caps:
        return None, None
    cap = min(caps, key=lambda c: _CONVICTION_ORDINAL[c[0].value])
    return cap[0], cap[1]


def grade(absolute_mean: float, flags: list[Flag], cap: Optional[Conviction],
          cfg: V4Config = CONFIG) -> Conviction:
    """Map absolute score x contradictions to AAA..B. Caps applied last.

    `base` est dérivé UNIQUEMENT de `absolute_mean` et `k`, sans jamais
    consulter `cap` pour le faire remonter (V4-02 : l'ancienne garde
    transformait un cap BBB restrictif en PLANCHER). Un cap ne peut que
    faire DESCENDRE base, jamais le contraire."""
    minors = sum(1 for f in flags if f.severity == "minor")
    majors = sum(1 for f in flags if f.severity == "major")
    k = minors + 2 * majors
    m = absolute_mean
    if m >= cfg.AAA_MIN and k == 0:
        base = Conviction.AAA
    elif m >= cfg.AA_MIN and k <= 1:
        base = Conviction.AA
    elif m >= cfg.A_MIN and k <= 1:
        base = Conviction.A
    elif m >= cfg.BBB_MIN and k <= 2:
        base = Conviction.BBB
    elif m >= cfg.BB_MIN:
        base = Conviction.BB
    else:
        base = Conviction.B
    if cap is not None:
        if _CONVICTION_ORDINAL[cap.value] < _CONVICTION_ORDINAL[base.value]:
            base = cap
    return base


# ════════════════════════════════════════════════════════════════════════════
# SECTION 12 — LEVELS + preflight
# ════════════════════════════════════════════════════════════════════════════
def _is_opposite(zone: ZoneView, direction: Direction) -> bool:
    side = (zone.side or "").upper()
    if direction is Direction.BULLISH:
        return side in ("SELL", "RESISTANCE", "SUPPLY")
    if direction is Direction.BEARISH:
        return side in ("BUY", "SUPPORT", "DEMAND")
    return False


def _get_opposite_zone(a: CanonicalAsset, direction: Direction) -> Optional[ZoneView]:
    zs = [z for z in a.zones if _is_opposite(z, direction)]
    return min(zs, key=lambda z: z.distance_pct) if zs else None


def atr_for_signal(a: CanonicalAsset, ev: Optional[StructureEventView]) -> tuple[float, str]:
    if ev is not None and a.mtf:
        tf = (ev.timeframe or "").upper()
        m = {"H1": a.mtf.atr_h1, "H4": a.mtf.atr_h4, "D1": a.mtf.atr_daily}
        v = m.get(tf)
        if v and v > 0:
            return float(v), f"atr_{tf.lower()}"
    return (a.atr_effective or 0.0), (a.atr_source or "h4")


def compute_entry(a: CanonicalAsset, ev: Optional[StructureEventView], atr: float,  # noqa: ARG001
                  cfg: V4Config) -> tuple[float, str]:
    price = a.current_price or 0.0
    if ev and ev.candles_elapsed <= 1 and (ev.distance_atr_multiple or 999) <= cfg.FRESH_ATR_MAX:
        return price, "Market"
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    z = a.nearest_aligned_zone
    if z and z.distance_pct <= cfg.LIMIT_ZONE_MAX_DIST:
        # La zone doit être devant le prix, pas derrière (pull-back attendu).
        zone_valid = (
            (direction is Direction.BULLISH and z.level < price) or
            (direction is Direction.BEARISH and z.level > price)
        )
        if zone_valid:
            return z.level, "Limit"
    if a.hot_zone_primary:
        hz = a.hot_zone_primary
        hz_valid = (
            (direction is Direction.BULLISH and hz.level < price) or
            (direction is Direction.BEARISH and hz.level > price)
        )
        if hz_valid:
            return hz.level, "Limit"
    return price, "Market"


def compute_sl(a: CanonicalAsset, entry: float, atr: float,
               ev: Optional[StructureEventView], cfg: V4Config) -> tuple[float, float, str]:
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    bb_regime = ev.bb_regime if ev else "Normal"
    bb_mult = cfg.BB_REGIME_MULT.get(bb_regime, cfg.DEFAULT_BB_MULT)
    if direction is Direction.BULLISH:
        sl_raw = entry - atr * bb_mult
    elif direction is Direction.BEARISH:
        sl_raw = entry + atr * bb_mult
    else:
        sl_raw = entry
    sl = sl_raw
    detail = f"Raw SL={sl_raw:.5f} ({bb_regime} ×{bb_mult})"
    z = a.nearest_aligned_zone
    if z and z.distance_pct <= cfg.LIMIT_ZONE_MAX_DIST:
        # P2-B — borne : sans elle, une zone mal placée produit un SL très
        # large avec un RR formellement acceptable.
        max_dist = cfg.SL_MAX_ATR_MULT * atr if atr > 0 else float("inf")
        if direction is Direction.BULLISH:
            cand = z.level - 0.3 * atr
            if cand < sl_raw:
                if abs(entry - cand) <= max_dist:
                    sl = cand
                    detail += f" zone-adj→{sl:.5f}"
                else:
                    detail += (f" [zone-adj REFUSÉ : {abs(entry - cand) / atr:.2f}×ATR "
                               f"> {cfg.SL_MAX_ATR_MULT}×ATR]")
            else:
                detail += f" [zone {z.level:.5f} au-dessus du SL, pas d'ajustement]"
        elif direction is Direction.BEARISH:
            cand = z.level + 0.3 * atr
            if cand > sl_raw:
                if abs(entry - cand) <= max_dist:
                    sl = cand
                    detail += f" zone-adj→{sl:.5f}"
                else:
                    detail += (f" [zone-adj REFUSÉ : {abs(entry - cand) / atr:.2f}×ATR "
                               f"> {cfg.SL_MAX_ATR_MULT}×ATR]")
            else:
                detail += f" [zone {z.level:.5f} sous le SL, pas d'ajustement]"
    min_dist = atr * cfg.SL_FLOOR_MULT
    if abs(entry - sl) < min_dist:
        sl = entry - min_dist if direction is Direction.BULLISH else entry + min_dist
        detail += f" [floored {cfg.SL_FLOOR_MULT}×ATR]"
    return sl, bb_mult, detail


def compute_tp1(a: CanonicalAsset, entry: float, atr: float,
                cfg: V4Config) -> tuple[float, Optional[float], bool]:
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    opp = _get_opposite_zone(a, direction)
    if opp:
        dist_atr = abs(opp.level - entry) / atr if atr > 0 else float("inf")
        if dist_atr <= cfg.TP_MAX_ATR_MULT:
            # Zone opposée proche et réaliste → l'utiliser comme TP1
            return opp.level, round(dist_atr, 2), False
        # Zone trop loin → réservée à TP2, TP1 synthétique
    tp1 = entry + cfg.TP1_ATR_MULT * atr if direction is Direction.BULLISH else entry - cfg.TP1_ATR_MULT * atr
    return tp1, cfg.TP1_ATR_MULT, True


def compute_tp2(a: CanonicalAsset, entry: float, tp1: float, atr: float,
                cfg: V4Config) -> tuple[Optional[float], Optional[float], bool]:
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    opp = [z for z in sorted(a.zones, key=lambda z: z.distance_pct)
           if _is_opposite(z, direction)]
    for z in opp:
        dist_atr = abs(z.level - entry) / atr if atr > 0 else float("inf")
        if dist_atr > cfg.TP_MAX_ATR_MULT:
            return z.level, round(dist_atr, 2), False
    tp2 = tp1 + cfg.TP2_ATR_MULT * atr if direction is Direction.BULLISH else tp1 - cfg.TP2_ATR_MULT * atr
    return tp2, (round(abs(tp2 - entry) / atr, 2) if atr > 0 else None), True


def compute_rr(entry: float, sl: float, tp1: float, tp2: Optional[float],
               tp1_syn: bool, tp2_syn: bool) -> tuple[float, str]:
    risk = abs(entry - sl)
    if risk <= 0 or math.isclose(risk, 0.0, abs_tol=1e-12):
        return 0.0, "Risk ~0, invalid"
    r1 = abs(tp1 - entry)
    if tp2 is None:
        rr = r1 / risk
        detail = f"RR(TP1 only)={rr:.2f}"
    else:
        r2 = abs(tp2 - entry)
        rr = (0.6 * r1 + 0.4 * r2) / risk
        detail = f"RR=(0.6×{r1:.5f}+0.4×{r2:.5f})/{risk:.5f}={rr:.2f}"
    flags = []
    if tp1_syn:
        flags.append("TP1 synth 2×ATR")
    if tp2_syn:
        flags.append("TP2 synth")
    if flags:
        detail += " [" + ", ".join(flags) + "]"
    return round(rr, 2), detail


@dataclass
class LevelBundle:
    entry: float
    entry_type: str
    sl: float
    sl_atr_multiple: float
    sl_detail: str
    tp1: float
    tp1_atr_multiple: Optional[float]
    tp1_synthetic: bool
    tp2: Optional[float]
    tp2_atr_multiple: Optional[float]
    tp2_synthetic: bool
    rr: float
    rr_detail: str
    atr_effective: float
    atr_source: str
    trigger: Optional[StructureEventView]
    rr_if_market: Optional[float] = None


def build_levels(a: CanonicalAsset, cfg: V4Config = CONFIG) -> LevelBundle:
    ev = _aligned_trigger(a)
    atr, atr_src = atr_for_signal(a, ev)
    entry, entry_type = compute_entry(a, ev, atr, cfg)
    sl, sl_mult, sl_detail = compute_sl(a, entry, atr, ev, cfg)
    tp1, tp1_mult, tp1_syn = compute_tp1(a, entry, atr, cfg)
    tp2, tp2_mult, tp2_syn = compute_tp2(a, entry, tp1, atr, cfg)
    rr, rr_detail = compute_rr(entry, sl, tp1, tp2, tp1_syn, tp2_syn)
    # P2-C — divulgation : RR si exécution au marché courant plutôt qu'à la limite.
    rr_if_market: Optional[float] = None
    price = a.current_price or 0.0
    if entry_type == "Limit" and price > 0:
        rr_if_market, _ = compute_rr(price, sl, tp1, tp2, tp1_syn, tp2_syn)
    return LevelBundle(
        entry=round(entry, 5), entry_type=entry_type,
        sl=round(sl, 5), sl_atr_multiple=sl_mult, sl_detail=sl_detail,
        tp1=round(tp1, 5), tp1_atr_multiple=tp1_mult, tp1_synthetic=tp1_syn,
        tp2=(round(tp2, 5) if tp2 is not None else None),
        tp2_atr_multiple=tp2_mult, tp2_synthetic=tp2_syn,
        rr=rr, rr_detail=rr_detail,
        atr_effective=atr, atr_source=atr_src, trigger=ev,
        rr_if_market=rr_if_market,
    )


def preflight(setup: SetupV4, cfg: V4Config = CONFIG) -> SetupV4:
    if setup.cal_status is CalStatus.BLACKOUT:
        setup.reject_code = "CAL_BLACKOUT"
        setup.reject_detail = setup.cal_note
        return setup
    if setup.atr_effective <= 0:
        setup.reject_code = "NO_ATR"
        setup.reject_detail = "ATR ≤ 0"
        return setup
    if setup.rr < cfg.RR_MIN or setup.rr > cfg.RR_MAX:
        setup.reject_code = "RR_OUT_OF_RANGE"
        setup.reject_detail = f"RR {setup.rr} ∉ [{cfg.RR_MIN},{cfg.RR_MAX}]"
        return setup
    if setup.direction is Direction.BULLISH and setup.sl >= setup.entry:
        setup.reject_code = "SL_SIGN"
        setup.reject_detail = "SL ≥ entry (bullish)"
        return setup
    if setup.direction is Direction.BEARISH and setup.sl <= setup.entry:
        setup.reject_code = "SL_SIGN"
        setup.reject_detail = "SL ≤ entry (bearish)"
        return setup
    # Rejeter si le prix courant a déjà atteint/dépassé TP1 (zone Limit stale).
    if setup.current_price > 0 and setup.atr_effective > 0:
        atr_overshoot = abs(setup.current_price - setup.entry) / setup.atr_effective
        if setup.direction is Direction.BULLISH and setup.current_price >= setup.tp1:
            setup.reject_code = "PRICE_PAST_TP"
            setup.reject_detail = (
                f"Prix {setup.current_price:.5f} ≥ TP1 {setup.tp1:.5f} "
                f"(entry dépassée de +{atr_overshoot:.2f}×ATR)"
            )
            return setup
        if setup.direction is Direction.BEARISH and setup.current_price <= setup.tp1:
            setup.reject_code = "PRICE_PAST_TP"
            setup.reject_detail = (
                f"Prix {setup.current_price:.5f} ≤ TP1 {setup.tp1:.5f} "
                f"(entry dépassée de +{atr_overshoot:.2f}×ATR)"
            )
            return setup
    # Conviction minimum post-decay.
    min_ord = _CONVICTION_ORDINAL.get(cfg.MIN_CONVICTION, 0)
    setup_ord = _CONVICTION_ORDINAL.get(setup.conviction.value, 0)
    if setup_ord < min_ord:
        setup.reject_code = "LOW_CONVICTION"
        setup.reject_detail = (
            f"Conviction {setup.conviction.value} < minimum {cfg.MIN_CONVICTION} "
            f"(score={setup.factor_scores.absolute_mean:.4f} "
            f"raw={setup.factor_scores.absolute_mean_raw:.4f} "
            f"decay={setup.factor_scores.decay_factor:.4f})"
        )
        return setup
    return setup


# ════════════════════════════════════════════════════════════════════════════
# SECTION 13 — DIVERSIFY  (cluster -> representative -> caps -> top N)
# ════════════════════════════════════════════════════════════════════════════
def _split_symbol(symbol: str) -> tuple[str, str]:
    if "/" in symbol:
        b, q = symbol.split("/", 1)
        return b, q
    return symbol, ""


def assign_clusters(setups: list[SetupV4], themes: MarketThemes) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in setups:
        base, quote = _split_symbol(s.symbol)
        d = s.direction.value
        inv = "Bearish" if d == "Bullish" else "Bullish"
        key = None
        if base in themes.strong and themes.strong[base] == d:
            key = f"{base}_{'strong' if d == 'Bullish' else 'weak'}"
        elif quote and quote in themes.strong and themes.strong[quote] == inv:
            key = f"{quote}_{'weak' if d == 'Bullish' else 'strong'}"
        if key is None:
            key = f"isolated:{s.symbol}"
        out[s.symbol] = key
        s.cluster = key
    return out


def _build_correlation_index(correlation_groups: Optional[dict]) -> dict[str, set[str]]:
    idx: dict[str, set[str]] = defaultdict(set)
    for key, members in (correlation_groups or {}).items():
        if not isinstance(members, list):
            continue
        for m in members:
            sym = (m or {}).get("symbol") if isinstance(m, dict) else None
            if sym:
                idx[str(sym)].add(str(key))
    return idx


def diversify(setups: list[SetupV4], themes: MarketThemes,
              cfg: V4Config = CONFIG, *,
              correlation_groups: Optional[dict] = None) -> list[SetupV4]:
    if not setups:
        return []
    assign_clusters(setups, themes)
    groups: dict[str, list[SetupV4]] = defaultdict(list)
    for s in setups:
        groups[s.cluster].append(s)
    representatives: list[SetupV4] = []
    for key, members in groups.items():
        members_sorted = sorted(
            members,
            key=lambda x: (-x.factor_scores.absolute_mean,
                           -_CONVICTION_ORDINAL[x.conviction.value],
                           x.symbol))
        rep = members_sorted[0]
        representatives.append(rep)
        for loser in members_sorted[1:]:
            loser.reject_code = "CLUSTER_DUP"
            loser.reject_detail = f"Représentant cluster {key} = {rep.symbol}"
    ranked = sorted(
        representatives,
        key=lambda x: (-_CONVICTION_ORDINAL[x.conviction.value],
                       -x.factor_scores.absolute_mean,
                       x.symbol))
    corr_idx = _build_correlation_index(correlation_groups)
    net: Counter = Counter()
    kept: list[SetupV4] = []
    kept_meta: list[tuple[str, str, str, set[str], Direction]] = []

    # reached_max : les représentants au-delà de MAX_SETUPS reçoivent un
    # vrai reject_code (MAX_SETUPS_REACHED) au lieu de tomber sur le
    # defaulting "CLUSTER_DUP" de _eliminated_from_setups(). DÉPLOIEMENT
    # ATOMIQUE avec les routes correspondantes dans selection_grid.py.
    reached_max = False
    for s in ranked:
        if reached_max:
            s.capped_reason = s.capped_reason or "limite MAX_SETUPS atteinte"
            s.reject_code = s.reject_code or "MAX_SETUPS_REACHED"
            continue

        base, quote = _split_symbol(s.symbol)
        sign = 1 if s.direction is Direction.BULLISH else -1

        over_base = abs(net[base] + sign) > cfg.MAX_EXPOSURE_PER_CCY
        over_quote = bool(quote) and abs(net[quote] - sign) > cfg.MAX_EXPOSURE_PER_CCY
        if over_base or over_quote:
            s.capped_reason = "exposition devise"
            s.reject_code = "EXPOSURE_CAP"
            s.cal_note = (s.cal_note + " [capped: exposition devise]").strip()
            continue

        # P2-A — cap corrélation UNIQUEMENT entre paires SANS devise commune
        # (celles avec devise commune sont déjà gouvernées par MAX_EXPOSURE_PER_CCY).
        s_groups = corr_idx.get(s.symbol, set())
        corr_hit: Optional[str] = None
        if s_groups:
            for k_sym, k_base, k_quote, k_groups, k_dir in kept_meta:
                shared = {base, quote} & {k_base, k_quote}
                shared.discard("")
                if shared:
                    continue
                common = s_groups & k_groups
                if common and _dir_eq(s.direction, k_dir):
                    corr_hit = f"{sorted(common)[0]} ~ {k_sym}"
                    break
        if corr_hit:
            s.capped_reason = f"corrélation groupe {corr_hit}"
            s.reject_code = "CORRELATION_CAP"
            s.cal_note = (s.cal_note + f" [capped: corrélation {corr_hit}]").strip()
            continue

        net[base] += sign
        if quote:
            net[quote] -= sign
        kept.append(s)
        kept_meta.append((s.symbol, base, quote, s_groups, s.direction))
        if len(kept) >= cfg.MAX_SETUPS:
            reached_max = True
    return kept


# ════════════════════════════════════════════════════════════════════════════
# SECTION 14 — PIPELINE
# ════════════════════════════════════════════════════════════════════════════
def _build_universe(assets: Mapping[str, CanonicalAsset], cal: CalendarSets,
                    cfg: V4Config) -> Universe:
    # R5 — audit des devises sans couverture calendaire : un silence sur
    # JPY/AUD/NZD/CHF n'est pas une absence de risque.
    all_ccy: set[str] = set()
    for a in assets.values():
        all_ccy.add(a.base)
        if a.quote:
            all_ccy.add(a.quote)
    covered_ccy: set[str] = {
        e.currency
        for e in list(cal.blackout) + list(cal.proximity) + list(cal.watch)
    }
    uncovered = all_ccy - covered_ccy
    if uncovered:
        logger.info(
            "R5 devises sans couverture calendaire (f7_macro retourne 1.0 par défaut): %s",
            sorted(uncovered),
        )

    passed: list[CanonicalAsset] = []
    rejected: list[tuple[CanonicalAsset, GateCode, str]] = []
    for asset in assets.values():
        if asset.mtf is None:
            rejected.append((asset, GateCode.G0_SCHEMA_ASSET_ERROR, "MTF manquant"))
            continue
        base, quote = asset.base, (asset.quote or "")
        if base in cal.suspended_ccy or quote in cal.suspended_ccy:
            hit = ({base, quote} & cal.suspended_ccy)
            note = f"Blackout: {sorted(hit)}"
            if cal.time_degraded:
                note += f" [fenêtres élargies ±{abs(cal.time_offset_hours):.1f}h — P0-A]"
            rejected.append((asset, GateCode.G1_CAL_BLACKOUT, note))
            continue
        quality = asset.mtf.quality or ""
        if quality not in cfg.MIN_QUALITY:
            rejected.append((asset, GateCode.G2_LOW_QUALITY, f"Quality {quality}"))
            continue
        if asset.mtf.direction is Direction.NEUTRAL:
            rejected.append((asset, GateCode.G3_NO_DIRECTION, "Direction Neutral"))
            continue
        if asset.mtf.pct < cfg.MIN_CONSENSUS_PCT:
            rejected.append((asset, GateCode.G4_LOW_CONSENSUS, f"MTF {asset.mtf.pct}%"))
            continue
        if asset.atr_effective is None or asset.atr_effective <= 0:
            rejected.append((asset, GateCode.G5_NO_ATR, f"ATR {asset.atr_source}"))
            continue
        passed.append(asset)
    return Universe(passed=passed, rejected=rejected)


def _compute_cal_status(a: CanonicalAsset, cal: Optional[CalendarSets]) -> tuple[CalStatus, str]:
    if cal is None:
        return CalStatus.OK, ""
    sides = {a.base, (a.quote or "")}
    hit_black = sides & cal.suspended_ccy
    if hit_black:
        names = [f"{e.currency} {e.event_name}" for e in cal.blackout if e.currency in hit_black]
        return CalStatus.BLACKOUT, "; ".join(names[:3])
    hit_prox = sides & cal.proximity_ccy
    if hit_prox:
        return CalStatus.PROXIMITY, ", ".join(sorted(hit_prox))
    hit_watch = sides & cal.watch_ccy
    if hit_watch:
        return CalStatus.WATCH, ", ".join(sorted(hit_watch))
    return CalStatus.OK, ""


def _scenario_hint(a: CanonicalAsset, lv: LevelBundle) -> str:
    """Descriptive label only — NOT a scoring pivot in V4."""
    parts = []
    age_known = (a.mtf is not None and a.mtf.age_d1 is not None)
    age = int(a.mtf.age_d1) if (a.mtf and age_known) else 0
    if lv.trigger is not None:
        ev = lv.trigger
        parts.append(f"CHoCH {ev.timeframe} {ev.candles_elapsed}c score={ev.confluence_score:.0f}")
    elif a.hot_zone_primary:
        parts.append("Hot Zone")
    if not age_known:
        parts.append("âge inconnu")
    elif age <= 15:
        parts.append("trend frais")
    elif age <= 30:
        parts.append("trend mûr")
    else:
        parts.append(f"trend âgé {age}j")
    parts.append(lv.entry_type)
    return " · ".join(parts)


def _htf_aligned(a: CanonicalAsset) -> bool:
    if a.mtf is None:
        return False
    d1 = a.mtf.biases.get("D1", "")
    h4 = a.mtf.biases.get("H4", "")
    dt = a.mtf.direction.value.lower()
    return dt in d1.lower() and dt in h4.lower()


def _rationale(a: CanonicalAsset, fv: FactorVector, themes: MarketThemes,
               flags: list[Flag], lv: Optional[LevelBundle] = None) -> str:
    if lv is None:
        lv = build_levels(a)
    parts = [f"Score absolu {fv.absolute_mean:.2f}"]
    top = sorted(fv.present, key=lambda n: -fv.get(n))[:3]
    parts.append("forts: " + ", ".join(f"{n.split('_')[0].upper()}={fv.get(n):.2f}" for n in top))
    if lv.trigger:
        ev = lv.trigger
        parts.append(f"trigger {ev.direction.value} {ev.timeframe} ({ev.session}, {ev.bb_regime})")
    if a.mtf:
        tb = themes.bonus_for(a.base, a.quote, a.mtf.direction)
        if tb > 0.6:
            parts.append(f"thème favorable ({tb:.2f})")
    ctx = a.market_context or {}
    market_state = ctx.get("market_state")
    if market_state and market_state not in ("DATA_INCOMPLETE", "RANGE_COMPRESSION"):
        parts.append(f"état {market_state}")
    risk_drivers = ctx.get("structural_risk_drivers") or []
    if risk_drivers:
        parts.append(f"risque: {risk_drivers[0]}")
    if flags:
        parts.append("flags: " + ", ".join(f.code for f in flags))
    return " · ".join(parts)


def _best_choch_info(a: CanonicalAsset) -> Optional[str]:
    """Label court du meilleur CHoCH Fresh (aligné d'abord, puis score desc).
    Format "<TF> <Dir> <Score> (<candles>c)", suffixe ⚠contra si contraire
    à la direction du trade. None si aucun CHoCH Fresh."""
    if not a.structure_events:
        return None
    fresh = [ev for ev in a.structure_events if ev.status.lower() == "fresh"]
    if not fresh:
        return None
    trade_dir = a.mtf.direction if a.mtf else None
    def _sort_key(ev: StructureEventView) -> tuple:
        aligned = int(_dir_eq(ev.direction, trade_dir)) if trade_dir else 0
        return (-aligned, -(ev.confluence_score or 0))
    best = sorted(fresh, key=_sort_key)[0]
    tf = best.timeframe or "?"
    score = int(best.confluence_score or 0)
    candles = best.candles_elapsed
    label = f"{tf} {best.direction.value} {score} ({candles}c)"
    if trade_dir and not _dir_eq(best.direction, trade_dir):
        label += " ⚠contra"
    return label


def _invalidation_structure(a: CanonicalAsset, cfg: V4Config) -> str:
    d = a.mtf.direction if a.mtf else Direction.NEUTRAL
    opp = _opposite_dir(d)
    ev = _aligned_trigger(a)
    tf = (ev.timeframe.upper() if (ev and ev.timeframe) else "D1")
    if tf not in cfg.HWA_WEIGHTS:
        tf = "D1"
    txt = f"CHoCH {opp.value} confirmé sur {tf} ou timeframe plus senior"
    cur = ((a.market_context or {}).get("structure_events_summary") or {}).get("highest_counter_tf")
    if cur:
        txt += f" — counter déjà présent sur {cur}"
    return txt


def _build_invalidation_contract(a, lv, cal, clock, horizon_days, horizon_event,
                                 cfg: V4Config) -> dict[str, str]:
    """P1-D — sortie pure : toutes les composantes sont déjà calculées ailleurs."""
    if clock is None:
        return {}
    if horizon_days is not None and horizon_days > 0:
        dl = clock.now_utc + timedelta(days=cfg.INVALIDATION_TIME_MULT * horizon_days)
        time_txt = (f"{dl.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"(×{cfg.INVALIDATION_TIME_MULT} horizon {horizon_days:.1f} j)")
    else:
        time_txt = "UNKNOWN — horizon non calculable"
    ev_txt = horizon_event or "aucun event S/A sur base ou quote dans l'horizon"
    return {
        "price": f"{lv.sl:.5f} (stop)",
        "time": time_txt,
        "event": (f"contrat EXPIRE à : {ev_txt}" if horizon_event else ev_txt),
        "structure": _invalidation_structure(a, cfg),
    }


def _make_draft(a: CanonicalAsset, fv: FactorVector, themes: MarketThemes,  # noqa: ARG001
                cal: Optional[CalendarSets], cfg: V4Config,
                lv: Optional[LevelBundle] = None,
                clock: Optional[Clock] = None) -> SetupV4:
    if lv is None:
        lv = build_levels(a, cfg)
    cal_status, cal_note = _compute_cal_status(a, cal)
    age_known = (a.mtf is not None and a.mtf.age_d1 is not None)          # P1-A
    age_val = int(a.mtf.age_d1) if (a.mtf and a.mtf.age_d1 is not None) else 0
    h_days = _target_horizon_days(a, lv, cfg)                              # P0-B
    ev_days, ev_label = (_next_macro_event_days(a, cal, clock) if clock else (None, None))
    fs = FactorScores(
        f1_hwa=round(fv.get("f1_hwa"), 4),
        f2_rmg=round(fv.get("f2_rmg"), 4),
        f3_ext=round(fv.get("f3_ext"), 4),
        f4_trg=round(fv.get("f4_trg"), 4),
        f5_xctx=round(fv.get("f5_xctx"), 4),
        f6_theme=round(fv.get("f6_theme"), 4),
        f7_macro=round(fv.get("f7_macro"), 4),
        absolute_mean=round(fv.absolute_mean, 4),
        quantile=0.0,
        missing=list(fv.missing),
        details={n: f.detail for n, f in fv.factors.items()},
    )
    return SetupV4(
        symbol=a.symbol,
        direction=(a.mtf.direction if a.mtf else Direction.NEUTRAL),
        scenario_hint=_scenario_hint(a, lv),
        entry=lv.entry, entry_type=lv.entry_type,
        sl=lv.sl, sl_atr_multiple=lv.sl_atr_multiple,
        tp1=lv.tp1, tp1_atr_multiple=lv.tp1_atr_multiple,
        tp2=lv.tp2, tp2_atr_multiple=lv.tp2_atr_multiple,
        rr=lv.rr, rr_synthetic=(lv.tp1_synthetic or lv.tp2_synthetic),
        tp1_synthetic=lv.tp1_synthetic, tp2_synthetic=lv.tp2_synthetic,
        atr_effective=lv.atr_effective, atr_source=lv.atr_source,
        distance_atr=(lv.trigger.distance_atr_multiple or 0.0) if lv.trigger else 0.0,
        choch_score=(lv.trigger.confluence_score if lv.trigger else None),
        choch_info=_best_choch_info(a),
        gps_quality=(a.mtf.quality if a.mtf else None),
        mtf_pct=(a.mtf.pct if a.mtf else 0),
        rsi_h4=_rsi_value(a, "H4"), rsi_h4_status=a.rsi_h4_status,
        cal_status=cal_status, cal_note=cal_note,
        htf_aligned=_htf_aligned(a),
        sl_detail=lv.sl_detail, rr_detail=lv.rr_detail,
        factor_scores=fs,
        current_price=(a.current_price or 0.0),
        asset_class=a.asset_class,
        age_d1=age_val, age_known=age_known,
        rr_if_market=lv.rr_if_market,
        horizon_days=(round(h_days, 2) if h_days is not None else None),
        horizon_event=ev_label,
        horizon_event_days=(round(ev_days, 2) if ev_days is not None else None),
        invalidation=_build_invalidation_contract(a, lv, cal, clock, h_days, ev_label, cfg),
    )


def _pipeline_factors_and_grades(
    universe: Universe,
    themes: MarketThemes,
    cal_sets: CalendarSets,
    clock: Clock,
    config: V4Config,
    regime: MacroRegime = MacroRegime.UNKNOWN,
) -> tuple[list[FactorVector], list[SetupV4], dict[str, LevelBundle]]:
    """Etapes 5-7 : factor vectors, drafts, quantiles, contradictions, grade."""
    vectors: list[FactorVector] = []
    drafts: list[SetupV4] = []
    lv_cache: dict[str, LevelBundle] = {}
    for a in universe.passed:
        fv = build_factor_vector(a, themes, cal_sets, clock, config)
        vectors.append(fv)
        lv = build_levels(a, config)
        lv_cache[a.symbol] = lv
        drafts.append(_make_draft(a, fv, themes, cal_sets, config, lv, clock))
    # Quantiles cross-section sur absolute_mean BRUT (rang relatif, le decay
    # ne doit pas biaiser l'ordre d'urgence intra-univers).
    quantiles = compute_quantiles(vectors)
    for s in drafts:
        s.factor_scores.quantile = round(quantiles.get(s.symbol, 0.0), 4)
    asset_by_sym = {a.symbol: a for a in universe.passed}
    fv_by_sym = {v.symbol: v for v in vectors}
    known_ages = [s.age_d1 for s in drafts if s.age_known]
    unknown_decay, unknown_src = resolve_unknown_decay(known_ages, config)
    n_unknown = sum(1 for s in drafts if not s.age_known)
    if n_unknown:
        logger.info("P1-A %d actif(s) à âge inconnu — decay = %.4f (%s, n_ref=%d)",
                    n_unknown, unknown_decay, unknown_src, len(known_ages))
    for s in drafts:
        a = asset_by_sym[s.symbol]
        fv = fv_by_sym[s.symbol]
        if s.age_known:
            decay, decay_src = _alpha_decay_factor(s.age_d1, config), "age"
        else:
            decay, decay_src = unknown_decay, unknown_src
        raw_mean = s.factor_scores.absolute_mean
        decayed_mean = _clamp01(raw_mean * decay)
        s.factor_scores.absolute_mean_raw = round(raw_mean, 4)
        s.factor_scores.decay_factor = round(decay, 4)
        s.factor_scores.absolute_mean = round(decayed_mean, 4)
        s.factor_scores.decay_source = decay_src
        flags = detect_contradictions(a, fv, themes, cal_sets, config,
                                      now=clock.now_utc,
                                      horizon=(s.horizon_days, s.horizon_event_days, s.horizon_event))
        # C11 câblé ici : le prédicat a besoin de s.entry_type (connu depuis
        # _make_draft, pas encore disponible dans detect_contradictions).
        stale_flag = _c11_stale_price_market_entry(a, s.entry_type)
        if stale_flag is not None:
            flags.append(stale_flag)
        s.flags = [FlagModel(code=f.code, severity=f.severity, detail=f.detail) for f in flags]
        cap, cap_reason = apply_caps(a, fv, config, flags=flags, regime=regime)
        if cap_reason:
            s.capped_reason = cap_reason
        s.conviction = grade(decayed_mean, flags, cap, config)
        s.rationale = _rationale(a, fv, themes, flags, lv_cache.get(s.symbol))
    return vectors, drafts, lv_cache


def _pipeline_rank_and_diversify(
    drafts: list[SetupV4],
    themes: MarketThemes,
    config: V4Config,
    correlation_groups: Optional[dict] = None,
) -> tuple[list[SetupV4], list[SetupV4], list[SetupV4]]:
    """Etapes 8-10 : preflight, rank, diversify. Retourne (final, preflight_rejects, ranked)."""
    for s in drafts:
        preflight(s, config)
    valid = [s for s in drafts if s.reject_code is None]
    preflight_rejects = [s for s in drafts if s.reject_code is not None]
    ranked = rank_setups(valid, config)
    final = diversify(ranked, themes, config, correlation_groups=correlation_groups)
    return final, preflight_rejects, ranked


def _pipeline_collect_eliminated(
    universe: Universe,
    preflight_rejects: list[SetupV4],
    ranked: list[SetupV4],
    final: list[SetupV4],
    cal: Optional[CalendarSets] = None,
) -> list[Eliminated]:
    """Etape 11 : collecte des actifs éliminés (gates + preflight + non-représentants)."""
    eliminated = _collect_eliminated(universe, cal)
    eliminated.extend(_eliminated_from_setups(preflight_rejects))
    final_syms = {s.symbol for s in final}
    non_reps = [s for s in ranked if s.symbol not in final_syms]
    eliminated.extend(_eliminated_from_setups(non_reps))
    return eliminated


def run_pipeline(
    merged_path: str,
    calendar_json_path: Optional[str] = None,
    output_path: Optional[str] = None,
    pdf_path: Optional[str] = None,
    config: V4Config = CONFIG,
) -> str:
    # 1 — ingestion
    meta, assets, correlation_groups = load_merged(merged_path)
    calendar_data = load_calendar(calendar_json_path, desk_generated_at=meta.generated_at)
    clock = Clock.from_meta(meta.generated_at)

    if output_path is None:
        output_path = report_filename(meta.generated_at, "html")
    if pdf_path is None:
        pdf_path = report_filename(meta.generated_at, "pdf")

    # 2 — calendar buckets
    cal_sets = calendar_data.bucket(clock.now_utc)

    # 3 — universe gates
    universe = _build_universe(assets, cal_sets, config)

    # 4 — themes
    themes = detect_currency_themes(assets, config)

    regime = classify_macro_regime(cal_sets, clock, config)
    logger.info("P1-C régime macro portefeuille = %s", regime.value)

    # 5-7 — factor vectors, drafts, grades
    vectors, drafts, lv_cache = _pipeline_factors_and_grades(
        universe, themes, cal_sets, clock, config, regime)

    # 8-10 — preflight, rank, diversify
    final, preflight_rejects, ranked = _pipeline_rank_and_diversify(
        drafts, themes, config, correlation_groups)

    # 11 — collect eliminated
    eliminated = _pipeline_collect_eliminated(
        universe, preflight_rejects, ranked, final, cal_sets)

    # 12 — render (HTML)
    html = render_report(final, eliminated, meta, clock, cal_sets, themes,
                         n_passed=len(universe.passed), cfg=config,
                         correlation_groups=correlation_groups,
                         macro_regime=regime,
                         cal_time_degraded=calendar_data.time_degraded,
                         cal_time_detail=calendar_data.time_audit_detail,
                         cal_feed_truncated=calendar_data.feed_horizon_truncated,
                         cal_feed_detail=calendar_data.feed_coverage_detail,
                         cal_stale=calendar_data.stale,
                         cal_stale_detail=calendar_data.stale_detail,
                         cal_merge_stale=calendar_data.merge_stale,
                         cal_merge_stale_detail=calendar_data.merge_stale_detail,
                         cal_covered_currencies=calendar_data.covered_currencies,
                         cal_feed_end_utc=calendar_data.feed_end_utc,
                         cal_feed_horizon_h=calendar_data.feed_horizon_h,
                         version=__version__)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    # 12b — render (PDF natif) — optionnel, jamais bloquant. fallback_html
    # explicite : sans lui, render_pdf ne produirait rien si WeasyPrint absent.
    if pdf_path:
        _fb = pdf_path[:-4] + ".html" if pdf_path.lower().endswith(".pdf") else pdf_path + ".html"
        render_pdf(html, pdf_path, fallback_html=_fb)
    return html


def _collect_eliminated(universe: Universe, cal: Optional[CalendarSets] = None) -> list[Eliminated]:
    # cal_status calculé via _compute_cal_status (vrai statut mesuré, pas le
    # défaut OK du modèle).
    out: list[Eliminated] = []
    for asset, code, detail in universe.rejected:
        m = asset.mtf
        h4 = asset.rsi_by_tf.get("H4") if asset.rsi_by_tf else None
        cal_status, _ = _compute_cal_status(asset, cal)
        out.append(Eliminated(
            symbol=asset.symbol,
            direction=(m.direction if m else Direction.NEUTRAL),
            reject_code=code.value, reject_detail=detail,
            rsi_h4=(_safe_float(h4.get("value")) if isinstance(h4, dict) else None),
            age_d1=((m.age_d1 or 0) if m else 0),
            age_known=(m is not None and m.age_d1 is not None),
            cal_status=cal_status,
            asset_class=asset.asset_class,
        ))
    return out


def _eliminated_from_setups(setups: list[SetupV4]) -> list[Eliminated]:
    return [Eliminated(
        symbol=s.symbol, direction=s.direction, scenario=s.scenario_hint,
        reject_code=(s.reject_code or "CLUSTER_DUP"),
        reject_detail=(s.reject_detail or s.capped_reason or "non-représentant cluster"),
        rsi_h4=s.rsi_h4, age_d1=s.age_d1, cal_status=s.cal_status, rr=s.rr,
        age_known=s.age_known,
        asset_class=s.asset_class,
    ) for s in setups]


# ════════════════════════════════════════════════════════════════════════════
# SECTION 15 — INGESTION
# ════════════════════════════════════════════════════════════════════════════
def load_merged(merged_path: str) -> tuple[MergeMeta, dict[str, CanonicalAsset], dict]:
    with open(merged_path, encoding="utf-8") as f:
        raw = json.load(f)
    meta = MergeMeta.model_validate(raw.get("meta", {}))
    # Vérification version schéma merge
    if meta.version:
        try:
            min_version = "3.4.0"
            meta_v = tuple(int(x) for x in meta.version.split(".")[:3])
            min_v = tuple(int(x) for x in min_version.split(".")[:3])
            if meta_v < min_v:
                logger.warning("Schéma merge obsolète: %s (minimum recommandé: %s)", meta.version, min_version)
        except (ValueError, AttributeError):
            logger.warning("Version schéma non parseable: %s", meta.version)
    else:
        logger.warning("Version schéma absente dans le merge")
    assets: dict[str, CanonicalAsset] = {}
    for sym, a in (raw.get("assets") or {}).items():
        try:
            assets[sym] = CanonicalAsset.model_validate(a)
        except Exception as exc:  # noqa: BLE001
            logger.warning("asset %s skipped: %s", sym, exc)
    # correlation_groups : passage brut, jamais bloquant si absent/malformé.
    raw_corr = raw.get("correlation_groups")
    correlation_groups: dict = raw_corr if isinstance(raw_corr, dict) else {}
    return meta, assets, correlation_groups


def load_calendar(calendar_json_path: Optional[str], desk_generated_at: Optional[datetime] = None) -> CalendarData:
    """Charge calendar.json (wrapper Module 04 ou CalendarData natif).

    Priorité : ``events_engine`` (passés 72h + futurs) puis ``events`` (UI).

    L'audit P0-A (`audit_calendar_time_consistency`) utilise gen_at (horloge
    du flux calendaire) comme référence : c'est un test d'invariant de
    cohérence INTERNE du flux (fuseau/troncature), car algébriquement
    offset = ref − cal_gen quelle que soit ref. La fraîcheur Desk-vs-calendrier
    est un contrôle SÉPARÉ (CALENDAR_STALE / MERGE_STALE, deux conditions
    opposées jamais agrégées par abs()). `desk_generated_at=None` préserve
    l'ancien comportement pour les appelants qui ne le fournissent pas."""
    if not calendar_json_path:
        return CalendarData()
    with open(calendar_json_path, encoding="utf-8") as f:
        raw = f.read()

    raw_dict: dict = json.loads(raw)

    is_wrapper = "metadata" in raw_dict
    gen_at: Optional[datetime] = None

    if is_wrapper:
        events_raw: list[dict] = (
            raw_dict.get("events_engine")
            or raw_dict.get("events", [])
        )
        meta = raw_dict.get("metadata", {})

        if not events_raw:
            logger.warning(
                "calendar.json chargé avec 0 events "
                "(total_high_impact=%s, upcoming=%s). "
                "Vérifier la fenêtre temporelle du feed FF ou activer show_past.",
                meta.get("total_high_impact", "?"),
                meta.get("upcoming_count", "?"),
            )

        cal_events: list[CalendarEvent] = []
        for ev in events_raw:
            try:
                cal_events.append(CalendarEvent.model_validate(ev))
            except Exception as exc:  # noqa: BLE001
                logger.debug("calendar event skipped: %s", exc)

        gen_at = _parse_iso_utc(meta.get("generated_at_utc"))
        off, conc, tot = audit_calendar_time_consistency(events_raw, gen_at)
        if tot and (conc / tot) >= CAL_TIME_MIN_RATIO and abs(off) > CAL_TIME_TOL_H:
            time_degraded = True
            time_offset = off
            time_detail = (f"offset systématique {off:+.2f}h entre hours_until et "
                           f"datetime_utc ({conc}/{tot} événements) — fenêtres "
                           f"élargies de ±{abs(off):.1f}h (fail-closed)")
            logger.error("P0-A ALERTE FUSEAU : %s", time_detail)
        else:
            time_degraded, time_offset, time_detail = False, 0.0, ""

        # Fraîcheur — DEUX conditions OPPOSÉES, jamais confondues :
        # calendrier antérieur au Desk (CALENDAR_STALE) vs snapshot marché
        # antérieur au calendrier (MERGE_STALE). Affichage seul.
        stale, stale_age_h, stale_detail = False, 0.0, ""
        merge_stale, merge_stale_age_h, merge_stale_detail = False, 0.0, ""
        if desk_generated_at and gen_at:
            age_h = (desk_generated_at - gen_at).total_seconds() / 3600.0
            if age_h > CALENDAR_STALE_TOL_H:
                stale = True
                stale_age_h = age_h
                stale_detail = (
                    f"calendrier antérieur au Desk de {age_h:.2f}h "
                    f"(flux: {gen_at:%H:%M:%S} UTC, Desk: {desk_generated_at:%H:%M:%S} UTC) "
                    f"— données calendaires potentiellement périmées"
                )
                logger.warning("CALENDAR_STALE : %s", stale_detail)
            elif age_h < -MERGE_STALE_TOL_H:
                merge_stale = True
                merge_stale_age_h = -age_h
                merge_stale_detail = (
                    f"snapshot de marché antérieur au calendrier de {-age_h:.2f}h "
                    f"({-age_h * 60:.0f} min) — merge : {desk_generated_at:%H:%M:%S} UTC, "
                    f"calendrier : {gen_at:%H:%M:%S} UTC. Prix, ATR, RSI et horloge de "
                    f"scoring sont ceux du merge ; le calendrier est plus récent. "
                    f"Aucune fenêtre de blackout modifiée."
                )
                logger.warning("MERGE_STALE : %s", merge_stale_detail)

        _filters = meta.get("filters_applied") or {}
        _cov_raw = _filters.get("currencies") if isinstance(_filters, dict) else None
        covered = (sorted({str(c).upper() for c in _cov_raw})
                   if isinstance(_cov_raw, list) else [])

        data = CalendarData(events=cal_events,
                            time_degraded=time_degraded,
                            time_offset_hours=time_offset,
                            time_audit_detail=time_detail,
                            stale=stale,
                            stale_age_h=stale_age_h,
                            stale_detail=stale_detail,
                            merge_stale=merge_stale,
                            merge_stale_age_h=merge_stale_age_h,
                            merge_stale_detail=merge_stale_detail,
                            covered_currencies=covered,
                            reachable=bool(meta.get("reachable", True)),
                            feed_horizon_truncated=bool(meta.get("feed_horizon_truncated", False)),
                            feed_horizon_h=meta.get("feed_horizon_h"),
                            feed_coverage_detail=meta.get("feed_coverage_detail", ""))
    else:
        # Format CalendarData natif : validation directe
        data = CalendarData.model_validate_json(raw)

    # ── Horizon réel du flux vs fenêtre WATCH ───────────────────────────────
    # Aucune décision modifiée : on rend visible une limite de couverture.
    # Flux vide/non-reachable (wrapper) -> fail-closed.
    if data.events:
        _ref = gen_at if (is_wrapper and gen_at is not None) else data.parsed_at
        # Défensif : parsed_at peut être naïf sur un CalendarData natif mal formé.
        if _ref.tzinfo is None:
            _ref = _ref.replace(tzinfo=timezone.utc)
        _feed_end = max(ev.datetime_utc for ev in data.events)
        _horizon_h = (_feed_end - _ref).total_seconds() / 3600.0
        data.feed_end_utc = _feed_end
        data.feed_horizon_h = _horizon_h
        if _horizon_h < WATCH_MAX_H:
            data.feed_horizon_truncated = True
            data.feed_coverage_detail = (
                f"couverture du flux : {_ref:%d/%m %H:%M} → {_feed_end:%d/%m %H:%M} UTC "
                # {:+.0f} affiche le bon signe dans les deux cas (+5h, -28h).
                f"({_horizon_h:+.0f}h) < fenêtre WATCH {WATCH_MAX_H:.0f}h — attendu "
                f"pour un flux hebdomadaire. Au-delà du {_feed_end:%d/%m %H:%M} UTC, "
                f"l'absence d'événement au calendrier n'est PAS une absence de "
                f"risque : F7 MACRO reste en fail-closed et le cap prudentiel "
                f"s'applique"
            )
            logger.warning("FEED-HORIZON TRONQUÉ : %s", data.feed_coverage_detail)
    elif is_wrapper:
        # Flux vide ou non-reachable → broadcast fail-closed
        data.feed_horizon_truncated = True
        data.feed_coverage_detail = (
            "flux calendaire vide ou non accessible — risque NON écarté, "
            "pas risque nul (attendre une fenêtre complète ou vérifier la source)"
        )
        logger.warning("FEED TRONQUÉ OU VIDE : %s", data.feed_coverage_detail)

    data.raw_html_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return data


def report_filename(generated_at: datetime, ext: str) -> str:
    """Nom standard BLUESTAR FX Desk_Signal Report_YYYY.MM.DD.{ext} (fuseau Clock)."""
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    local = generated_at.astimezone(Clock._REPORT_TZ)  # pylint: disable=protected-access
    return f"BLUESTAR FX Desk_Signal Report_{local.strftime('%Y.%m.%d')}.{ext}"
