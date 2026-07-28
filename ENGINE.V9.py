"""
BLUESTAR ENGINE v10.4.3 — Production-Ready Signal Generation & Portfolio Risk Management
═══════════════════════════════════════════════════════════════════════════════════════════

Architecture: Hybrid Absolute/Cross-Sectional Scoring
  - 7 Factor Vector (F1..F7) + Decay + Flags (C1..C9) + Caps + Preflight + Diversification
  - Calendar Integration (R1-R5) with temporal audit
  - Correlation-aware clustering
  - HTML/PDF calibrated output (A4 institutional)

Changes v10.2.2 → v10.4.3:
  P0-A: Calendar temporal integrity detection + fail-closed widening
  P1-A: Age_d1=None optimism bias correction + DECAY_UNKNOWN
  P1-B: Freshness audit (FX-aware bar counting) + C9 flag
  P1-D: Invalidation contract (explicit termination conditions)
  P2-A: correlation_groups integrated into diversification
  P2-B: SL zone adjustment bounded by SL_MAX_ATR_MULT
  P2-C: RR sensitivity to market execution (rr_if_market)
  P0-B: Horizon coherence (C7) — deferred to v11 (structural refactor needed)

Compliance: Zero Regression on existing valid signals + institutional audit trail.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

import jinja2
from dateutil import parser
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger("bluestar.v10")

# ════════════════════════════════════════════════════════════════════════════
# SECTION 0 — OPTIONAL UPSTREAM (graceful fallback, never blocking)
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


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CALENDAR MODELS (v10.4.3: added calendar_time_degraded fields)
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
PROXIMITY_MAX_H = 48.0
WATCH_MAX_H = 168.0


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
    
    # v10.3.0: Temporal audit
    calendar_time_degraded: bool = False
    calendar_offset_hours: float = 0.0

    def bucket(self, now: datetime, margin_hours: float = 0.0) -> CalendarSets:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        
        blackout, proximity, watch = [], [], []
        for ev in self.events:
            if ev.impact != ImpactLevel.HIGH:
                continue
            
            before, after = TIER_WINDOWS.get(ev.tier, (2.0, 24.0))
            before_margin = before + margin_hours
            after_margin = after + margin_hours
            
            delta = (ev.datetime_utc - now).total_seconds() / 3600.0
            
            if -after_margin <= delta <= before_margin:
                blackout.append(ev)
            elif before_margin < delta <= PROXIMITY_MAX_H + margin_hours:
                proximity.append(ev)
            elif PROXIMITY_MAX_H + margin_hours < delta <= WATCH_MAX_H + margin_hours:
                watch.append(ev)
        
        return CalendarSets(blackout=blackout, proximity=proximity, watch=watch)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CANONICAL ASSET VIEW (unchanged core)
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
    
    # v10.3.2: Freshness audit
    signal_time: Optional[datetime] = None

    @field_validator("direction", mode="before")
    @classmethod
    def _d(cls, v: Any) -> Direction:
        return _norm_dir(v)
    
    @field_validator("signal_time")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
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
    rr_if_market: Optional[float] = None          # v10.4.3: Sensitivity to market exec
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
    age_known: bool = True                        # v10.3.1: Age validity flag
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
    invalidation_contract: dict[str, Any] = Field(default_factory=dict)  # v10.4.1


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
# SECTION 6 — CONFIG (v10.4.3: added HORIZON_*, SL_MAX_ATR_MULT, DECAY_UNKNOWN)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class V4Config:
    MIN_QUALITY: frozenset = frozenset({"A+", "A"})
    MIN_CONSENSUS_PCT: int = 50
    
    HWA_WEIGHTS: Mapping[str, int] = field(default_factory=lambda: MappingProxyType(
        {"MN": 6, "W1": 5, "D1": 4, "H4": 3, "H1": 2, "M15": 1}))
    
    RMG_FAST: tuple = ("M15", "H1")
    RMG_SLOW: tuple = ("D1", "W1")
    RMG_SCALE: float = 15.0
    RMG_MIN_TF: int = 3
    
    EXT_TF_COUNT: int = 5
    
    TRG_SCORE_CAP: float = 85.0
    TRG_FRESH_MAX: int = 6
    TRG_DIST_ATR_MAX: float = 1.0
    SR_BONUS_MAX: float = 0.20
    SR_DIST_MAX_PCT: float = 2.0
    SR_W_W1: float = 0.50
    SR_W_D1: float = 0.30
    SR_W_H4: float = 0.20
    
    THEME_MIN_VOTES: int = 3
    THEME_BULL_HI: float = 0.8
    THEME_BULL_LO: float = 0.2
    THEME_COHESION_C5: float = 0.8
    
    MACRO_TAU_HOURS: float = 48.0
    
    AAA_MIN: float = 0.80
    AA_MIN: float = 0.68
    A_MIN: float = 0.55
    BBB_MIN: float = 0.42
    BB_MIN: float = 0.30
    MACRO_CAP_RISK_THRESHOLD: float = 0.50
    
    DECAY_TIME_CONSTANT: int = 35
    DECAY_FLOOR: float = 0.30
    DECAY_UNKNOWN: float = 0.50               # v10.3.1: Neutral decay for null age_d1
    
    C1_TRG_MIN: float = 0.5
    C1_RMG_MAX: float = 0.35
    C2_EXT_MAX: float = 0.3
    C2_HWA_MAX: float = 0.5
    C4_DIST_ATR: float = 1.0
    
    # v10.4.0: Horizon coherence
    HORIZON_ATR_REALIZATION_RATE: float = 0.6
    HORIZON_MARGIN: float = 1.25
    
    RR_MIN: float = 1.5
    RR_MAX: float = 20.0
    SL_FLOOR_MULT: float = 0.8
    SL_MAX_ATR_MULT: float = 3.0                # v10.4.3: Borne SL par zone
    DEFAULT_BB_MULT: float = 1.5
    BB_REGIME_MULT: Mapping[str, float] = field(default_factory=lambda: MappingProxyType(
        {"Squeeze": 1.0, "Normal": 1.5, "Expansion": 2.0}))
    
    FRESH_ATR_MAX: float = 0.3
    LIMIT_ZONE_MAX_DIST: float = 2.0
    TP1_ATR_MULT: float = 2.0
    TP2_ATR_MULT: float = 1.0
    TP_MAX_ATR_MULT: float = 4.0
    
    MAX_SETUPS: int = 5
    MAX_EXPOSURE_PER_CCY: int = 2
    MIN_CONVICTION: str = "BB"

    @classmethod
    def from_dict(cls, d: dict) -> "V4Config":
        base = cls()
        kw = {}
        for k, v in (d or {}).items():
            if hasattr(base, k):
                kw[k] = v
        return cls(**kw)


CONFIG = V4Config()

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
# SECTION 8 — FACTORS F1..F7
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
    if a.mtf is None:
        return None
    want = a.mtf.direction
    cands = [ev for ev in a.structure_events
             if ev.status.lower() == "fresh" and _dir_eq(ev.direction, want)]
    if not cands:
        return None
    return min(cands, key=lambda e: e.candles_elapsed)


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


def f4_trg(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:
    ev = _aligned_trigger(a)
    if ev is None:
        return ScoredFactor("f4_trg", None, 0.0, True, "pas de trigger aligné")
    
    score_n = min(ev.confluence_score, cfg.TRG_SCORE_CAP) / cfg.TRG_SCORE_CAP
    fresh = 1.0 - min(ev.candles_elapsed, cfg.TRG_FRESH_MAX) / cfg.TRG_FRESH_MAX
    dist = ev.distance_atr_multiple if ev.distance_atr_multiple is not None else cfg.TRG_DIST_ATR_MAX
    proximity = 1.0 - min(dist, cfg.TRG_DIST_ATR_MAX) / cfg.TRG_DIST_ATR_MAX
    
    base = _clamp01(0.4 * score_n + 0.3 * fresh + 0.3 * proximity)
    sr_bonus, sr_detail = _sr_structure_bonus(a, cfg)
    score = _clamp01(base + sr_bonus)
    detail = (f"TRG score_n={score_n:.2f} fresh={fresh:.2f} prox={proximity:.2f} "
              f"(conf={ev.confluence_score:.0f}, {ev.candles_elapsed}c, {dist:.2f}ATR) | {sr_detail}")
    return ScoredFactor("f4_trg", ev.confluence_score, score, False, detail)


def _norm_lookup(v: Any) -> str:
    return str(v or "").strip().lower()


def f5_xctx(a: CanonicalAsset, cfg: V4Config = CONFIG) -> ScoredFactor:
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


def f6_theme(a: CanonicalAsset, themes: MarketThemes, cfg: V4Config = CONFIG) -> ScoredFactor:
    if a.mtf is None:
        return ScoredFactor("f6_theme", None, 0.5, True, "MTF absent")
    score = themes.bonus_for(a.base, a.quote, a.mtf.direction)
    detail = (f"THEME {a.base}/{a.quote or '—'} dir={a.mtf.direction.value} "
              f"strong={themes.strong} -> {score:.2f}")
    return ScoredFactor("f6_theme", None, score, False, detail)


def _parse_ff_value(s: Optional[str]) -> Optional[float]:
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
    if cal is None:
        return ScoredFactor("f7_macro", None, 1.0, False, "calendrier absent (risque nul)")
    
    sides = {a.base, (a.quote or "")}
    
    if sides & cal.suspended_ccy:
        return ScoredFactor("f7_macro", 1.0, 0.0, False, "BLACKOUT actif")
    
    now = clock.now_utc
    horizon: list[CalendarEvent] = list(cal.blackout) + list(cal.proximity) + list(cal.watch)
    
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
        base_score = 1.0
        base_detail = "aucun event S/A futur"
        base_risk = 0.0
    else:
        hours = min(relevant_h)
        base_risk = math.exp(-hours / cfg.MACRO_TAU_HOURS)
        base_score = _clamp01(1.0 - base_risk)
        base_detail = f"MACRO event S/A dans {hours:.1f}h risk={base_risk:.2f} -> {base_score:.2f}"
    
    residual_parts: list[str] = []
    residual_penalty = 0.0
    for ev in horizon:
        if ev.tier not in (EventTier.S, EventTier.A):
            continue
        if ev.currency not in sides:
            continue
        delta = (ev.datetime_utc - now).total_seconds() / 3600.0
        if delta >= 0:
            continue
        _, after = TIER_WINDOWS.get(ev.tier, (2.0, 24.0))
        if delta < -after:
            continue
        surprise = _surprise_factor(ev)
        recency = math.exp(delta / max(after / 3.0, 1.0))
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
        "f4_trg": f4_trg(a, cfg),
        "f5_xctx": f5_xctx(a, cfg),
        "f6_theme": f6_theme(a, themes, cfg),
        "f7_macro": f7_macro(a, cal, clock, cfg),
    }
    return FactorVector(symbol=a.symbol, factors=factors)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9 — SCORING & RANKING
# ════════════════════════════════════════════════════════════════════════════
def compute_quantiles(vectors: list[FactorVector]) -> dict[str, float]:
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


def rank_setups(setups: list[SetupV4], cfg: V4Config = CONFIG) -> list[SetupV4]:
    def key(s: SetupV4):
        fs = s.factor_scores
        return (
            -fs.absolute_mean,
            -fs.f4_trg,
            -fs.f1_hwa,
            -fs.f7_macro,
            -fs.quantile,
            s.symbol,
        )
    return sorted(setups, key=key)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 10 — CONTRADICTIONS C1..C9
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
    evs = (a.market_context or {}).get("structure_events_summary") or {}
    if not evs.get("escalation_detected"):
        return None
    seq = evs.get("escalation_sequence") or []
    seq_str = " → ".join(seq) if seq else "multi-TF"
    return Flag("C6", "major",
                f"Escalade structurelle counter-MTF : {seq_str}")


def _c8_age_unknown(a: CanonicalAsset) -> Optional[Flag]:
    """v10.3.1: Flag if age_d1 is unknown"""
    if a.mtf is None or a.mtf.age_d1 is not None:
        return None
    return Flag("C8", "minor", "Âge de structure inconnu — decay neutre appliqué")


def detect_contradictions(a: CanonicalAsset, fv: FactorVector, themes: MarketThemes,
                          cal: Optional[CalendarSets], cfg: V4Config = CONFIG) -> list[Flag]:
    flags: list[Flag] = []
    for f in (
        _c1_struct_vs_momentum(fv, cfg),
        _c2_momentum_vs_trend(fv, cfg),
        _c3_trend_vs_calendar(a, fv, cal, cfg),
        _c4_quality_vs_potential(a, cfg),
        _c5_trade_vs_theme(a, themes, cfg),
        _c6_structural_escalation(a),
        _c8_age_unknown(a),
    ):
        if f is not None:
            flags.append(f)
    return flags


# ════════════════════════════════════════════════════════════════════════════
# SECTION 11 — CAPS & GRADE
# ════════════════════════════════════════════════════════════════════════════
def apply_caps(a: CanonicalAsset, fv: FactorVector, cfg: V4Config = CONFIG
               ) -> tuple[Optional[Conviction], Optional[str]]:
    caps: list[tuple[Conviction, str]] = []
    
    if (a.conviction_cap or "").upper() == "BBB" or (a.atr_source or "").lower() == "synthetic":
        caps.append((Conviction.BBB, "ATR synthétique"))
    
    macro_risk = 1.0 - fv.get("f7_macro")
    if macro_risk >= cfg.MACRO_CAP_RISK_THRESHOLD:
        caps.append((Conviction.AA, f"risque macro élevé ({macro_risk:.2f})"))
    
    if (a.market_context or {}).get("structural_risk") == "Critical":
        caps.append((Conviction.BBB, "risque structurel critique (REVERSAL_RISK)"))
    
    if not caps:
        return None, None
    
    cap = min(caps, key=lambda c: _CONVICTION_ORDINAL[c[0].value])
    return cap[0], cap[1]


def grade(absolute_mean: float, flags: list[Flag], cap: Optional[Conviction],
          cfg: V4Config = CONFIG) -> Conviction:
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
    elif (m >= cfg.BBB_MIN or cap is not None) and k <= 2:
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
# SECTION 12 — LEVELS (v10.4.3: SL bounded, rr_if_market, invalidation_contract)
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


def compute_entry(a: CanonicalAsset, ev: Optional[StructureEventView], atr: float,
                  cfg: V4Config = CONFIG) -> tuple[float, str]:
    price = a.current_price or 0.0
    if ev and ev.candles_elapsed <= 1 and (ev.distance_atr_multiple or 999) <= cfg.FRESH_ATR_MAX:
        return price, "Market"
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    z = a.nearest_aligned_zone
    if z and z.distance_pct <= cfg.LIMIT_ZONE_MAX_DIST:
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
               ev: Optional[StructureEventView], cfg: V4Config = CONFIG) -> tuple[float, float, str]:
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
        if direction is Direction.BULLISH:
            sl_candidate = z.level - 0.3 * atr
            # v10.4.3: Borne SL par zone
            if sl_candidate < sl_raw and abs(entry - sl_candidate) <= cfg.SL_MAX_ATR_MULT * atr:
                sl = sl_candidate
                detail += f" zone-adj→{sl:.5f}"
            elif abs(entry - sl_candidate) > cfg.SL_MAX_ATR_MULT * atr:
                detail += f" [zone SL rejeté: {abs(entry - sl_candidate) / atr:.1f}×ATR > {cfg.SL_MAX_ATR_MULT}]"
        elif direction is Direction.BEARISH:
            sl_candidate = z.level + 0.3 * atr
            if sl_candidate > sl_raw and abs(entry - sl_candidate) <= cfg.SL_MAX_ATR_MULT * atr:
                sl = sl_candidate
                detail += f" zone-adj→{sl:.5f}"
            elif abs(entry - sl_candidate) > cfg.SL_MAX_ATR_MULT * atr:
                detail += f" [zone SL rejeté: {abs(entry - sl_candidate) / atr:.1f}×ATR > {cfg.SL_MAX_ATR_MULT}]"
    
    min_dist = atr * cfg.SL_FLOOR_MULT
    if abs(entry - sl) < min_dist:
        sl = entry - min_dist if direction is Direction.BULLISH else entry + min_dist
        detail += f" [floored {cfg.SL_FLOOR_MULT}×ATR]"
    
    return sl, bb_mult, detail


def compute_tp1(a: CanonicalAsset, entry: float, atr: float,
                cfg: V4Config = CONFIG) -> tuple[float, Optional[float], bool]:
    direction = a.mtf.direction if a.mtf else Direction.NEUTRAL
    opp = _get_opposite_zone(a, direction)
    if opp:
        dist_atr = abs(opp.level - entry) / atr if atr > 0 else float("inf")
        if dist_atr <= cfg.TP_MAX_ATR_MULT:
            return opp.level, round(dist_atr, 2), False
    tp1 = entry + cfg.TP1_ATR_MULT * atr if direction is Direction.BULLISH else entry - cfg.TP1_ATR_MULT * atr
    return tp1, cfg.TP1_ATR_MULT, True


def compute_tp2(a: CanonicalAsset, entry: float, tp1: float, atr: float,
                cfg: V4Config = CONFIG) -> tuple[Optional[float], Optional[float], bool]:
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


def _compute_rr_if_market(
    entry_type: str,
    entry: float,
    current_price: float,
    sl: float,
    tp1: float,
    tp2: Optional[float],
) -> Optional[float]:
    """v10.4.3: RR if execution at current market price"""
    if entry_type != "Limit":
        return None
    risk = abs(current_price - sl)
    if risk <= 0:
        return None
    r1 = abs(tp1 - current_price)
    if tp2 is None:
        return round(r1 / risk, 2)
    else:
        r2 = abs(tp2 - current_price)
        return round((0.6 * r1 + 0.4 * r2) / risk, 2)


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


def build_levels(a: CanonicalAsset, cfg: V4Config = CONFIG) -> LevelBundle:
    ev = _aligned_trigger(a)
    atr, atr_src = atr_for_signal(a, ev)
    entry, entry_type = compute_entry(a, ev, atr, cfg)
    sl, sl_mult, sl_detail = compute_sl(a, entry, atr, ev, cfg)
    tp1, tp1_mult, tp1_syn = compute_tp1(a, entry, atr, cfg)
    tp2, tp2_mult, tp2_syn = compute_tp2(a, entry, tp1, atr, cfg)
    rr, rr_detail = compute_rr(entry, sl, tp1, tp2, tp1_syn, tp2_syn)
    return LevelBundle(
        entry=round(entry, 5), entry_type=entry_type,
        sl=round(sl, 5), sl_atr_multiple=sl_mult, sl_detail=sl_detail,
        tp1=round(tp1, 5), tp1_atr_multiple=tp1_mult, tp1_synthetic=tp1_syn,
        tp2=(round(tp2, 5) if tp2 is not None else None),
        tp2_atr_multiple=tp2_mult, tp2_synthetic=tp2_syn,
        rr=rr, rr_detail=rr_detail,
        atr_effective=atr, atr_source=atr_src, trigger=ev,
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
# SECTION 13 — TEMPORAL AUDIT (v10.3.0: fail-closed detection)
# ════════════════════════════════════════════════════════════════════════════
def audit_calendar_time_consistency(
    events_raw: list[dict],
    generated_at: datetime,
    tolerance_hours: float = 0.25
) -> tuple[float, int, bool]:
    """v10.3.0: Detect systematic offset between hours_until & datetime_utc"""
    if not events_raw:
        return 0.0, 0, False
    
    offsets = []
    for ev in events_raw:
        if "datetime_utc" not in ev or "hours_until" not in ev:
            continue
        try:
            dt_utc = parser.parse(ev["datetime_utc"]).replace(tzinfo=timezone.utc)
            hours_declared = float(ev["hours_until"])
            hours_real = (dt_utc - generated_at).total_seconds() / 3600.0
            offset = hours_declared - hours_real
            offsets.append(offset)
        except (ValueError, TypeError, AttributeError):
            continue
    
    if not offsets:
        return 0.0, 0, False
    
    offset_median = statistics.median(offsets)
    concordant = sum(1 for o in offsets if abs(o - offset_median) < tolerance_hours)
    is_systematic = (concordant / len(offsets)) >= 0.80 if offsets else False
    
    return offset_median, concordant, is_systematic


# ════════════════════════════════════════════════════════════════════════════
# SECTION 14 — DIVERSIFICATION (v10.4.2: correlation_groups support)
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


def diversify(setups: list[SetupV4], themes: MarketThemes,
              cfg: V4Config = CONFIG, correlation_groups: Optional[dict] = None) -> list[SetupV4]:
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
    
    net: Counter = Counter()
    kept: list[SetupV4] = []
    
    for s in ranked:
        base, quote = _split_symbol(s.symbol)
        sign = 1 if s.direction is Direction.BULLISH else -1
        
        over_base = abs(net[base] + sign) > cfg.MAX_EXPOSURE_PER_CCY
        over_quote = bool(quote) and abs(net[quote] - sign) > cfg.MAX_EXPOSURE_PER_CCY
        
        if over_base or over_quote:
            s.capped_reason = "exposition devise"
            s.cal_note = (s.cal_note + " [capped: exposition devise]").strip()
            continue
        
        # v10.4.2: Correlation check
        if correlation_groups:
            skip = False
            for group_key, group_members in correlation_groups.items():
                group_symbols = {m.get("symbol") for m in group_members if isinstance(m, dict)}
                if s.symbol in group_symbols:
                    kept_in_group = [ks for ks in kept if ks.symbol in group_symbols]
                    if kept_in_group:
                        s.capped_reason = f"corrélation groupe {group_key}"
                        s.cal_note = (s.cal_note + f" [capped: corr {group_key}]").strip()
                        skip = True
                        break
            if skip:
                continue
        
        net[base] += sign
        if quote:
            net[quote] -= sign
        kept.append(s)
        
        if len(kept) >= cfg.MAX_SETUPS:
            break
    
    return kept


# ════════════════════════════════════════════════════════════════════════════
# SECTION 15 — INVALIDATION CONTRACT (v10.4.1)
# ════════════════════════════════════════════════════════════════════════════
def _build_invalidation_contract(
    a: CanonicalAsset,
    lv: LevelBundle,
    cal: Optional[CalendarSets],
    clock: Clock,
    cfg: V4Config = CONFIG,
) -> dict[str, Any]:
    """v10.4.1: Explicit termination conditions for each setup"""
    contract = {
        "price": {
            "level": lv.sl,
            "label": f"Stop-Loss {lv.sl:.5f} ({lv.sl_atr_multiple:.1f}×ATR)"
        },
        "time": {
            "label": "Horizon expiré"
        },
        "event": {
            "label": "Événement majeur"
        },
        "structure": {
            "label": "CHoCH counter-direction détecté"
        }
    }
    
    # Calculate horizon
    if lv.tp1 and a.mtf and a.mtf.atr_daily and a.mtf.atr_daily > 0:
        distance = abs(lv.tp1 - lv.entry)
        realization_rate = cfg.HORIZON_ATR_REALIZATION_RATE
        days_to_target = distance / (realization_rate * a.mtf.atr_daily)
        contract["time"]["label"] = f"Horizon expiré (~{days_to_target:.1f} jours)"
        contract["time"]["days"] = round(days_to_target, 1)
    
    # Nearest major event
    if cal is not None:
        sides = {a.base, (a.quote or "")}
        relevant = [
            e for e in (list(cal.blackout) + list(cal.proximity))
            if e.tier in (EventTier.S, EventTier.A) and e.currency in sides
        ]
        if relevant:
            nearest = min(relevant, key=lambda e: (e.datetime_utc - clock.now_utc).total_seconds())
            contract["event"]["label"] = (
                f"{nearest.event_name} ({nearest.currency}) à "
                f"{nearest.datetime_utc.strftime('%d/%m %H:%M UTC')}"
            )
            contract["event"]["currency"] = nearest.currency
            contract["event"]["name"] = nearest.event_name
    
    # Counter CHoCH
    if a.market_context:
        counter = a.market_context.get("structure_events_summary", {}).get("counter_fresh_count", 0)
        if counter > 0:
            highest_tf = a.market_context.get("structure_events_summary", {}).get("highest_counter_tf")
            contract["structure"]["label"] = f"CHoCH {highest_tf} détecté (direction inverse)"
    
    return contract


# ════════════════════════════════════════════════════════════════════════════
# SECTION 16 — MAKEUP & RATIONALE (ported from v9)
# ════════════════════════════════════════════════════════════════════════════
def _scenario_hint(a: CanonicalAsset, lv: LevelBundle) -> str:
    parts = []
    age = (a.mtf.age_d1 or 0) if a.mtf else 0
    if lv.trigger is not None:
        ev = lv.trigger
        parts.append(f"CHoCH {ev.timeframe} {ev.candles_elapsed}c score={ev.confluence_score:.0f}")
    elif a.hot_zone_primary:
        parts.append("Hot Zone")
    if age <= 15:
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


def _make_draft(a: CanonicalAsset, fv: FactorVector, themes: MarketThemes,
                cal: Optional[CalendarSets], cfg: V4Config,
                lv: Optional[LevelBundle] = None, clock: Optional[Clock] = None) -> SetupV4:
    if lv is None:
        lv = build_levels(a, cfg)
    
    cal_status, cal_note = (CalStatus.BLACKOUT, "") if (a.base in (cal.suspended_ccy or set()) or (a.quote or "") in (cal.suspended_ccy or set())) else (CalStatus.OK, "")
    
    # v10.3.1: age_known flag
    age_d1 = a.mtf.age_d1 if a.mtf and a.mtf.age_d1 is not None else None
    age_known = age_d1 is not None
    
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
    
    # v10.4.3: rr_if_market sensitivity
    rr_if_market = _compute_rr_if_market(
        lv.entry_type, lv.entry, a.current_price or 0.0,
        lv.sl, lv.tp1, lv.tp2
    )
    
    # v10.4.1: invalidation_contract
    if clock is None:
        clock = Clock.from_meta(datetime.now(timezone.utc))
    invalidation_contract = _build_invalidation_contract(a, lv, cal, clock, cfg)
    
    return SetupV4(
        symbol=a.symbol,
        direction=(a.mtf.direction if a.mtf else Direction.NEUTRAL),
        scenario_hint=_scenario_hint(a, lv),
        entry=lv.entry, entry_type=lv.entry_type,
        sl=lv.sl, sl_atr_multiple=lv.sl_atr_multiple,
        tp1=lv.tp1, tp1_atr_multiple=lv.tp1_atr_multiple,
        tp2=lv.tp2, tp2_atr_multiple=lv.tp2_atr_multiple,
        rr=lv.rr, rr_synthetic=(lv.tp1_synthetic or lv.tp2_synthetic),
        rr_if_market=rr_if_market,
        atr_effective=lv.atr_effective, atr_source=lv.atr_source,
        distance_atr=(lv.trigger.distance_atr_multiple or 0.0) if lv.trigger else 0.0,
        choch_score=(lv.trigger.confluence_score if lv.trigger else None),
        choch_info=_best_choch_info(a),
        gps_quality=(a.mtf.quality if a.mtf else None),
        mtf_pct=(a.mtf.pct if a.mtf else 0),
        rsi_h4=_rsi_value(a, "H4"), rsi_h4_status=a.rsi_h4_status,
        age_d1=((a.mtf.age_d1 or 0) if a.mtf else 0),
        age_known=age_known,
        cal_status=cal_status, cal_note=cal_note,
        htf_aligned=_htf_aligned(a),
        sl_detail=lv.sl_detail, rr_detail=lv.rr_detail,
        factor_scores=fs,
        current_price=(a.current_price or 0.0),
        asset_class=a.asset_class,
        invalidation_contract=invalidation_contract,
    )


# ════════════════════════════════════════════════════════════════════════════
# SECTION 17 — PIPELINE & PRODUCTION ENTRY
# ════════════════════════════════════════════════════════════════════════════
def _build_universe(assets: Mapping[str, CanonicalAsset], cal_sets: CalendarSets,
                    config: V4Config) -> Universe:
    passed: list[CanonicalAsset] = []
    rejected: list[tuple[CanonicalAsset, GateCode, str]] = []
    
    all_ccy: set[str] = set()
    for a in assets.values():
        all_ccy.add(a.base)
        if a.quote:
            all_ccy.add(a.quote)
    
    covered_ccy: set[str] = {
        e.currency
        for e in list(cal_sets.blackout) + list(cal_sets.proximity) + list(cal_sets.watch)
    }
    uncovered = all_ccy - covered_ccy
    if uncovered:
        logger.info("R5 devises sans couverture calendaire: %s", sorted(uncovered))
    
    for asset in assets.values():
        if asset.mtf is None:
            rejected.append((asset, GateCode.G0_SCHEMA_ASSET_ERROR, "MTF manquant"))
            continue
        
        base, quote = asset.base, (asset.quote or "")
        if base in cal_sets.suspended_ccy or quote in cal_sets.suspended_ccy:
            hit = ({base, quote} & cal_sets.suspended_ccy)
            rejected.append((asset, GateCode.G1_CAL_BLACKOUT, f"Blackout: {sorted(hit)}"))
            continue
        
        quality = asset.mtf.quality or ""
        if quality not in config.MIN_QUALITY:
            rejected.append((asset, GateCode.G2_LOW_QUALITY, f"Quality {quality}"))
            continue
        
        if asset.mtf.direction is Direction.NEUTRAL:
            rejected.append((asset, GateCode.G3_NO_DIRECTION, "Direction Neutral"))
            continue
        
        if asset.mtf.pct < config.MIN_CONSENSUS_PCT:
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


def _pipeline_factors_and_grades(
    universe: Universe,
    themes: MarketThemes,
    cal_sets: CalendarSets,
    clock: Clock,
    config: V4Config,
    decay_unknown: Optional[float] = None,
) -> tuple[list[FactorVector], list[SetupV4], dict[str, LevelBundle]]:
    vectors: list[FactorVector] = []
    drafts: list[SetupV4] = []
    lv_cache: dict[str, LevelBundle] = {}
    
    for a in universe.passed:
        fv = build_factor_vector(a, themes, cal_sets, clock, config)
        vectors.append(fv)
        lv = build_levels(a, config)
        lv_cache[a.symbol] = lv
        drafts.append(_make_draft(a, fv, themes, cal_sets, config, lv, clock))
    
    quantiles = compute_quantiles(vectors)
    for s in drafts:
        s.factor_scores.quantile = round(quantiles.get(s.symbol, 0.0), 4)
    
    asset_by_sym = {a.symbol: a for a in universe.passed}
    fv_by_sym = {v.symbol: v for v in vectors}
    
    # Calibrate DECAY_UNKNOWN if not provided
    if decay_unknown is None:
        known_decays = []
        for a in universe.passed:
            if a.mtf and a.mtf.age_d1 is not None and a.mtf.age_d1 > 0:
                decay = math.exp(-a.mtf.age_d1 / config.DECAY_TIME_CONSTANT)
                decay = max(config.DECAY_FLOOR, decay)
                known_decays.append(decay)
        if known_decays:
            decay_unknown = statistics.median(known_decays)
        else:
            decay_unknown = config.DECAY_UNKNOWN
    
    for s in drafts:
        a = asset_by_sym[s.symbol]
        fv = fv_by_sym[s.symbol]
        
        # Compute decay
        if s.age_known:
            decay = math.exp(-s.age_d1 / config.DECAY_TIME_CONSTANT)
            decay = max(config.DECAY_FLOOR,
