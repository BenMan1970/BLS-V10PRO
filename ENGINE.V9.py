"""
BLUESTAR ENGINE v10 — Hybrid Absolute/Cross-Sectional (V4 architecture)
========================================================================
Single-file monolithic engine. Source of truth = merged JSON.

Pipeline logique INCHANGÉ vs version précédente (Pydantic, mode dégradé,
calendrier tiéré, ATR synthétique, preflight, audit trail, 7 facteurs V4,
moyenne absolue -> conviction, quantile -> tie-break/diversification).

NOUVEAUTÉ (couche rendu uniquement) :
  - Génération PDF NATIVE via WeasyPrint (moteur HTML/CSS->PDF maîtrisé)
    => document A4 calibré, marges uniformes, en-tête/pied paginés,
       anti-coupure des cartes (break-inside), échelle typographique cohérente.
  - Le HTML interactif reste disponible ; le bouton "print" est conservé
    en repli si WeasyPrint n'est pas installé.

Usage:
  # HTML
  python v10.py --merged merge.json --calendar-json calendar.json -o report.html
  # PDF natif calibré
  python v10.py --merged merge.json --calendar-json calendar.json --pdf report.pdf
  # API
  from v10 import run_pipeline, render_pdf
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
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

import jinja2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger("bluestar.v10")

# ════════════════════════════════════════════════════════════════════════════
# SECTION 0 — OPTIONAL upstream import (graceful fallback, never blocking)
# ════════════════════════════════════════════════════════════════════════════
try:
    from weasyprint import HTML as _WeasyHTML
    _HAS_WEASYPRINT = True
except Exception:
    _HAS_WEASYPRINT = False


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
    S = "S"
    A = "A"
    B = "B"
    NONE = "NONE"


class GateCode(str, Enum):
    PASS = "PASS"
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
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


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
        return f if f == f and f not in (float("inf"), float("-inf")) else None
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


_FX_WEEK_CLOSE_WEEKDAY = 4
_FX_WEEK_CLOSE_HOUR = 21
_FX_WEEK_CLOSED_HOURS = 48.0

_TF_ACTIVE_HOURS: Mapping[str, float] = MappingProxyType({
    "M15": 0.25, "M30": 0.5, "H1": 1.0, "H4": 4.0,
    "D1": 24.0, "DAILY": 24.0,
    "W1": 120.0, "WEEKLY": 120.0,
    "MN": 480.0, "MONTHLY": 480.0,
})


def _fx_active_hours(start: datetime, end: datetime) -> float:
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
    bar_h = _TF_ACTIVE_HOURS.get((timeframe or "").upper())
    if not bar_h or bar_h <= 0:
        return -1
    return int(_fx_active_hours(start, end) / bar_h)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CALENDAR MODELS
# ════════════════════════════════════════════════════════════════════════════
_TIER_S = ("non-farm", "nonfarm", "nfp", "fomc", "cpi", "cash rate",
           "bank rate", "rate statement", "interest rate", "monetary policy")
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


TIER_WINDOWS: Mapping[EventTier, tuple[float, float]] = MappingProxyType({
    EventTier.S: (4.0, 48.0),
    EventTier.A: (2.0, 24.0),
    EventTier.B: (1.0, 6.0),
})
DEFAULT_TIER_WINDOW = (2.0, 24.0)
PROXIMITY_MAX_H = 48.0
WATCH_MAX_H = 168.0

CAL_TIME_TOL_H = 0.25
CAL_TIME_MIN_RATIO = 0.80


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
    hours_until: Optional[float] = None
    priority: Optional[str] = None

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

    def bucket(self, now: datetime) -> CalendarSets:
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
                            time_offset_hours=self.time_offset_hours)


def audit_calendar_time_consistency(
    events_raw: list[dict],
    generated_at: Optional[datetime],
    tol_h: float = CAL_TIME_TOL_H,
) -> tuple[float, int, int]:
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
    age_d1: Optional[int] = 0
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
    signal_time: Optional[datetime] = None

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
    conviction_cap: Optional[str] = None
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

    @classmethod
    def from_meta(cls, generated_at: datetime) -> "Clock":
        now_utc = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=timezone.utc)
        now_local = now_utc.astimezone(timezone(timedelta(hours=1)))
        return cls(now_utc=now_utc, now_local=now_local,
                   date_hdr=now_local.strftime("%Y-%m-%d %H:%M GMT+1"))


# ════════════════════════════════════════════════════════════════════════════
# SECTION 5 — V4 MODELS & DATACLASSES
# ════════════════════════════════════════════════════════════════════════════
_FACTOR_NAMES = ("f1_hwa", "f2_rmg", "f3_ext", "f4_trg", "f5_xctx", "f6_theme", "f7_macro")


@dataclass(frozen=True)
class ScoredFactor:
    name: str
    raw: Optional[float]
    score: float
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
    code: str
    severity: str
    detail: str


@dataclass
class RiskCluster:
    key: str
    members: list[str] = field(default_factory=list)


class FactorScores(BaseModel):
    model_config = ConfigDict(extra="ignore")
    f1_hwa: float = 0.0
    f2_rmg: float = 0.0
    f3_ext: float = 0.0
    f4_trg: float = 0.0
    f5_xctx: float = 0.0
    f6_theme: float = 0.0
    f7_macro: float = 0.0
    absolute_mean: float = 0.0
    absolute_mean_raw: float = 0.0
    decay_factor: float = 1.0
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
    atr_effective: float = 0.0
    atr_source: str = "unknown"
    distance_atr: float = 0.0
    choch_score: Optional[float] = None
    choch_info: Optional[str] = None
    gps_quality: Optional[str] = None
    mtf_pct: int = 0
    rsi_h4: Optional[float] = None
    rsi_h4_status: Optional[str] = None
    age_d1: int = 0
    age_known: bool = True
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
    current_price: float = 0.0
    asset_class: str = "forex"
    horizon_days: Optional[float] = None
    horizon_event: Optional[str] = None
    horizon_event_days: Optional[float] = None
    invalidation: dict[str, str] = Field(default_factory=dict)
    rr_if_market: Optional[float] = None


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
    age_known: bool = True
    cal_status: CalStatus = CalStatus.OK
    rr: Optional[float] = None
    asset_class: str = "forex"


@dataclass
class MarketThemes:
    strong: dict[str, str] = field(default_factory=dict)
    cohesion: dict[str, float] = field(default_factory=dict)

    def bonus_for(self, base: str, quote: Optional[str], direction: Direction) -> float:
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
            return 0.5
        signed = sum(contributions) / len(contributions)
        return _clamp01((signed + 1.0) / 2.0)

    def is_counter_theme(self, base: str, quote: Optional[str], direction: Direction) -> tuple[bool, float]:
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
    MIN_QUALITY: frozenset = frozenset({"A+", "A"})
    MIN_CONSENSUS_PCT: int = 50
    HWA_WEIGHTS: Mapping[str, int] = field(default_factory=lambda: MappingProxyType(
        {"MN": 6, "W1": 5, "D1": 4, "H4": 3, "H1": 2, "M15": 1}))
    RMG_FAST:
