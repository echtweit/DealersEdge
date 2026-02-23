"""
Centralized configuration for all tunable parameters.

Every magic number, threshold, and regime boundary lives here.
Frozen dataclasses ensure these are immutable at runtime.
Changing a parameter means changing it in ONE place.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AcfConfig:
    SHORT_GAMMA_CEILING: float = 0.11
    MIN_DAY_BARS: int = 20
    MIN_TOTAL_BARS: int = 30
    DEFAULT_MAX_LAG: int = 10
    SCAN_MAX_LAG: int = 5
    VARIANCE_FLOOR: float = 1e-12

    REGIME_LONG_GAMMA_THRESHOLD: float = -0.05
    REGIME_SHORT_GAMMA_THRESHOLD: float = 0.05

    SLOPE_DEEPENING: float = -0.005
    SLOPE_SHALLOWING: float = 0.005
    MIN_POINTS_FOR_SLOPE: int = 3

    STABILITY_ROCK_SOLID: float = 0.10
    STABILITY_STABLE: float = 0.25
    STABILITY_CONTESTED: float = 0.40

    SQUEEZE_PROXIMITY: float = 0.85

    SEI_THRESHOLD_PCT: float = 0.1
    SEI_MIN_RETURNS: int = 20
    SEI_MIN_CLUSTER_SIZE: int = 2
    SEI_SCORE_MULTIPLIER: float = 10000
    SEI_HIGH: float = 150
    SEI_MODERATE: float = 80
    SEI_LOW: float = 40


@dataclass(frozen=True)
class ReynoldsConfig:
    ATM_TOLERANCE: float = 0.10
    IV_FALLBACK: float = 0.3
    IV_FLOOR: float = 0.01
    DEALER_GAMMA_ZERO_GUARD: float = 1e-6
    RE_CAP: float = 99

    REGIME_TURBULENT: float = 1.0
    REGIME_TRANSITIONAL: float = 0.7

    PHASE_THRESHOLD_PCT: float = 12.9
    PHASE_APPROACHING_FACTOR: float = 0.7
    PHASE_DEFAULT_WINDOW: int = 20


@dataclass(frozen=True)
class GexConfig:
    SHARES_PER_CONTRACT: int = 100
    TIME_FLOOR: float = 0.001
    IV_FLOOR: float = 0.01
    IV_FALLBACK: float = 0.3
    DEFAULT_RISK_FREE_RATE: float = 0.05
    CHARM_TIME_FLOOR: float = 0.001

    ENTROPY_ATM_RANGE_PCT: float = 10.0
    ENTROPY_MIN_STRIKES: int = 3
    ENTROPY_CRITICAL: float = 0.3
    ENTROPY_APPROACHING: float = 0.5
    ENTROPY_MODERATE: float = 0.7


@dataclass(frozen=True)
class ChannelConfig:
    FLOOR_THRESHOLD: float = 0.15
    CEILING_THRESHOLD: float = 0.15
    FALLBACK_THRESHOLD: float = 0.10
    MIN_WIDTH_PCT: float = 1.0
    WIDEN_THRESHOLD: float = 0.05


@dataclass(frozen=True)
class VolConfig:
    HV_WINDOWS: tuple = (10, 20, 30, 60)
    ANNUALIZATION_FACTOR: int = 252
    MIN_CLOSES: int = 20
    MIN_CLOSES_PERCENTILE: int = 60
    ROLLING_HV_WINDOW: int = 20
    ATM_IV_FALLBACK: float = 0.3
    HV_COMPARE_MIN: float = 1.0

    RATIO_CHEAP: float = 0.80
    RATIO_SLIGHT_DISCOUNT: float = 0.95
    RATIO_FAIR: float = 1.10
    RATIO_SLIGHT_PREMIUM: float = 1.30
    RATIO_EXPENSIVE: float = 1.60

    SKEW_OTM_SHORT_DTE: float = 0.05
    SKEW_OTM_LONG_DTE: float = 0.08
    SKEW_SHORT_DTE_CUTOFF: int = 14
    SKEW_HIGH_PUT: float = 0.15
    SKEW_MODERATE_PUT: float = 0.05
    SKEW_FLAT_LOW: float = -0.05
    SKEW_CALL: float = -0.15

    TERM_CONTANGO: float = 2.0
    TERM_MILD_CONTANGO: float = 0.5
    TERM_MILD_BACKWARDATION: float = -0.5
    TERM_BACKWARDATION: float = -2.0
    TERM_SLOPE_PCT_THRESHOLD: float = 5.0
    TERM_MIN_POINTS: int = 2

    # VRP thresholds (GEX-adjusted variance points × 100)
    VRP_HIGH_PREMIUM: float = 5.0
    VRP_MODERATE_PREMIUM: float = 2.0
    VRP_SMALL_PREMIUM: float = 0.0
    VRP_FAIR: float = -2.0
    VRP_MIN_HV: float = 0.01
    VRP_MIN_DTE_FOR_DRAG: int = 5

    # GEX vol multipliers
    GEX_VOL_POS_LAMINAR: float = 0.78
    GEX_VOL_POS_OTHER: float = 0.88
    GEX_VOL_NEG_TURBULENT: float = 1.15
    GEX_VOL_NEG_OTHER: float = 1.08

    # Vol Edge weights (score component caps)
    EDGE_IV_HV_CHEAP: int = 40
    EDGE_IV_HV_SLIGHT: int = 30
    EDGE_IV_HV_FAIR: int = 20
    EDGE_IV_HV_SLIGHT_PREM: int = 10
    EDGE_TERM_CONTANGO: int = 25
    EDGE_TERM_MILD: int = 15
    EDGE_TERM_FLAT: int = 10
    EDGE_TERM_BACKWARDATION: int = -10
    EDGE_VRP_DISCOUNT: int = 15
    EDGE_VRP_FAIR: int = 8
    EDGE_VRP_SMALL: int = 3
    EDGE_VRP_MODERATE: int = -5
    EDGE_VRP_HIGH: int = -10
    EDGE_SCORE_MIN: int = 0
    EDGE_SCORE_MAX: int = 100
    EDGE_STRONG_BUY: int = 60
    EDGE_BUY: int = 40
    EDGE_NEUTRAL: int = 25
    EDGE_EXPENSIVE: int = 10


@dataclass(frozen=True)
class DirectionalConfig:
    BETA_FLOOR: float = 0.3

    # Thesis tree thresholds
    ENTROPY_UPGRADE_RE: float = 1.05
    EFFECTIVE_RE_TURBULENT: float = 1.0
    EFFECTIVE_RE_LAMINAR: float = 0.7
    ACF_NET_MOMENTUM_PCT: float = 13.0
    ACF_MOMENTUM_THRESHOLD: float = 0.05
    ACF_SHORT_GAMMA: float = 0.05
    ACF_FADE_STRONG: float = -0.10

    # Wall break probability
    WB_BASE_PROB: float = 15.0
    WB_RE_EXTREME: float = 2.0
    WB_RE_EXTREME_BOOST: float = 45
    WB_RE_TURBULENT_BOOST: float = 30
    WB_RE_TRANSITIONAL_BOOST: float = 15
    WB_RE_MILD_BOOST: float = 5
    WB_ACF_STRONG_BOOST: float = 10
    WB_ACF_MILD_BOOST: float = 5
    WB_ACF_MILD_PENALTY: float = -5
    WB_ACF_STRONG_PENALTY: float = -10
    WB_PHASE_TURBULENT_BOOST: float = 10
    WB_PHASE_APPROACHING_BOOST: float = 5
    WB_SEI_HIGH_BOOST: float = 8
    WB_SEI_MODERATE_BOOST: float = 4
    WB_ENTROPY_CRITICAL_BOOST: float = 10
    WB_ENTROPY_APPROACHING_BOOST: float = 5
    WB_GAMMA_ASYMMETRY_POSITIVE: float = -12
    WB_GAMMA_ASYMMETRY_NEGATIVE: float = 5
    WB_PROB_MIN: float = 5.0
    WB_PROB_MAX: float = 95.0

    # IV context thresholds
    IV_HIGH: float = 0.60
    IV_MODERATE: float = 0.35

    # Channel position thresholds
    CH_NEAR_FLOOR: float = 0.25
    CH_NEAR_CEILING: float = 0.75

    # Charm drift
    CHARM_DRIFT_THRESHOLD: float = -10000
    CHARM_DRIFT_MAX_DTE: int = 5

    # Strike rounding
    STRIKE_BRACKETS: tuple = ((500, 5.0), (100, 5.0), (50, 2.5), (20, 1.0), (0, 0.5))

    # Kelly sizing
    KELLY_EDGE_FLOOR: float = 0.5
    KELLY_WIN_PROB_CAP: float = 0.9
    KELLY_HALF: float = 0.5
    KELLY_PCT_MIN: float = 0.25
    KELLY_PCT_MAX: float = 5.0
    KELLY_EXPENSIVE_IV_HV: float = 1.5
    KELLY_CHEAP_IV_HV: float = 0.9
    KELLY_EXPENSIVE_SCALE: float = 0.6
    KELLY_CHEAP_SCALE: float = 1.2
    KELLY_FULL_CONVICTION: float = 3.0
    KELLY_STANDARD: float = 1.5
    KELLY_REDUCED: float = 0.75

    # ATR move
    ATR_CLAMP_MULTIPLIER: float = 1.5
    ATR_TARGET_MULTIPLIER: float = 2.0

    # Level consolidation
    LEVEL_CONSOLIDATION_PCT: float = 0.3

    # Tech override thresholds
    TECH_SCORE_OVERRIDE: int = 2
    MA_ALIGNMENT_OVERRIDE: int = 3


@dataclass(frozen=True)
class StraddleConfig:
    OTM_CALL_MULT: float = 1.03
    OTM_PUT_MULT: float = 0.97
    ATM_IV_FALLBACK: float = 0.3

    IV_RV_CHEAP: float = 0.85
    IV_RV_EXPENSIVE: float = 1.15

    # Sub-score clamps
    SCORE_MIN: int = 0
    SCORE_MAX: int = 25

    # Regime score thresholds
    RE_TURBULENT_SCORE: int = 12
    RE_TRANSITIONAL_SCORE: int = 6
    ACF_STRONG_AMP_SCORE: int = 8
    ACF_MILD_AMP_SCORE: int = 5
    ACF_NEUTRAL_SCORE: int = 2
    ACF_DAMP_PENALTY: int = -3
    PCT_AMP_HIGH: float = 15.0
    PCT_AMP_HIGH_SCORE: int = 5
    PCT_AMP_MID: float = 10.0
    PCT_AMP_MID_SCORE: int = 3

    # IV score thresholds
    IV_RV_VERY_CHEAP: float = 0.75
    IV_RV_CHEAP_SCORE: int = 20
    IV_RV_MILD_CHEAP: float = 0.85
    IV_RV_MILD_CHEAP_SCORE: int = 15
    IV_RV_FAIR_SCORE: int = 10
    IV_RV_SLIGHT_PREM: float = 1.15
    IV_RV_SLIGHT_PREM_SCORE: int = 5
    IV_RV_MODERATE_PREM: float = 1.30
    IV_RV_MODERATE_PREM_SCORE: int = 2
    ATM_IV_LOW: float = 0.20
    ATM_IV_LOW_SCORE: int = 5
    ATM_IV_MID: float = 0.30
    ATM_IV_MID_SCORE: int = 3

    # Catalyst thresholds
    PHASE_DIST_NEAR: float = 5.0
    CHANNEL_TIGHT: float = 2.0
    CHANNEL_MID: float = 4.0
    SEI_CATALYST_HIGH: float = 150
    SEI_CATALYST_HIGH_SCORE: int = 6
    SEI_CATALYST_MID: float = 80
    SEI_CATALYST_MID_SCORE: int = 3

    # Structural thresholds
    NEG_GAMMA_SCORE: int = 8
    POS_GAMMA_SCORE: int = 2
    CH_WIDTH_TIGHT: float = 3.0
    CH_WIDTH_TIGHT_SCORE: int = 5
    CH_WIDTH_MID: float = 5.0
    CH_WIDTH_MID_SCORE: int = 3
    RE_HIGH: float = 1.5
    RE_HIGH_SCORE: int = 4
    RE_MID_SCORE: int = 2
    ATR_COVERAGE_HIGH: float = 1.5
    ATR_COVERAGE_HIGH_SCORE: int = 8
    ATR_COVERAGE_MID: float = 1.0
    ATR_COVERAGE_MID_SCORE: int = 5
    ATR_COVERAGE_LOW: float = 0.7
    ATR_COVERAGE_LOW_SCORE: int = 2
    ENTROPY_CRITICAL_SCORE: int = 8
    ENTROPY_APPROACHING_SCORE: int = 4

    # VRP drag
    VRP_HIGH_DRAG: int = -12
    VRP_MODERATE_DRAG: int = -7
    VRP_SMALL_DRAG: int = -3
    VRP_DISCOUNT_BONUS: int = 8

    # Verdict thresholds
    VERDICT_BUY_STRADDLE: int = 70
    VERDICT_BUY_GOOD: int = 60
    VERDICT_CONSIDER: int = 45
    VERDICT_MAX_IV_RV_STRADDLE: float = 1.2
    VERDICT_MAX_IV_RV_GOOD: float = 1.3
    VERDICT_STRANGLE_BE_PCT: float = 3.5

    # Warning thresholds
    BREAKEVEN_WARNING_PCT: float = 3.0
    STRANGLE_SAVINGS_THRESHOLD: float = 30.0
    ATR_COVERAGE_WARNING: float = 0.7

    # Move probability
    MOVE_PROB_MIN_BARS: int = 30
    MOVE_PROB_MIN_EXTRA: int = 5

    # Theta schedule
    THETA_MAX_DAYS: int = 21
    THETA_TIME_FLOOR: float = 0.5


@dataclass(frozen=True)
class CollisionConfig:
    REGIME_MULT_TURBULENT: float = 0.6
    REGIME_MULT_TRANSITIONAL: float = 0.8
    REGIME_MULT_LONG_GAMMA: float = 1.4

    URGENCY_NOW: float = 1.0
    URGENCY_IMMINENT: float = 2.0

    PROB_MIN: float = 0.01
    PROB_MAX: float = 0.99


@dataclass(frozen=True)
class TechnicalsConfig:
    MA_PERIODS: tuple = (20, 50, 200)
    MA_SLOPE_WINDOW: int = 5
    MA_SLOPE_RISING: float = 0.1
    MA_SLOPE_FALLING: float = -0.1
    CROSS_DIFF_PCT: float = 1.5

    ATR_PERIOD: int = 14
    ATR_EXPANDING: float = 10.0
    ATR_CONTRACTING: float = -10.0
    ATR_RECENT_DAYS: int = 5

    RS_WINDOWS: tuple = (5, 20, 60)
    RS_STRONG_LEADER: float = 3.0
    RS_OUTPERFORMING: float = 1.0
    RS_IN_LINE_LOW: float = -1.0
    RS_UNDERPERFORMING: float = -3.0
    RS_TREND_IMPROVING: float = 1.0
    RS_TREND_DETERIORATING: float = -1.0
    BETA_FLOOR: float = 0.3
    BETA_VAR_FLOOR: float = 1e-10
    MIN_BARS_RS: int = 20

    VWAP_PERIODS: tuple = (5, 20)
    VWAP_EXTENDED_ABOVE: float = 2.0
    VWAP_ABOVE: float = 0.5
    VWAP_BELOW: float = -0.5
    VWAP_EXTENDED_BELOW: float = -2.0

    TREND_STRONG_UPTREND_ALIGNMENT: int = 2
    TREND_STRONG_DOWNTREND_ALIGNMENT: int = -2
    HIGH_VOL_ATR: float = 3.0
    NORMAL_VOL_ATR: float = 1.5


# Singleton instances — import these in modules
ACF = AcfConfig()
REYNOLDS = ReynoldsConfig()
GEX = GexConfig()
CHANNEL = ChannelConfig()
VOL = VolConfig()
DIRECTIONAL = DirectionalConfig()
STRADDLE = StraddleConfig()
COLLISION = CollisionConfig()
TECHNICALS = TechnicalsConfig()
