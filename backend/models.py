"""
Pydantic response models — enforces API response shapes.

FastAPI auto-validates every response against these models.
Any missing field, wrong type, or schema drift is caught immediately.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Any


# ── Sub-models ───────────────────────────────────────────────────────────────


class GexByStrike(BaseModel):
    strike: float
    call_oi: int
    put_oi: int
    call_gex: float
    put_gex: float
    net_gex: float
    call_delta: float
    put_delta: float
    net_dealer_delta: float
    call_charm: float
    put_charm: float
    net_charm: float
    call_vanna: float
    put_vanna: float
    net_vanna: float


class EntropyData(BaseModel):
    entropy: float
    entropy_norm: float
    regime: str
    description: str
    n_strikes: int
    top_concentrations: list = []


class AcfSelfExcitation(BaseModel):
    sei: float = 0
    regime: str = "NONE"
    description: str = ""
    n_clusters: int = 0
    avg_cluster_size: float = 0
    max_cluster_size: int = 0
    total_excitation_events: int = 0


class AcfData(BaseModel):
    mean_acf1: float = 0
    pct_dampened: float = 0
    pct_amplified: float = 0
    stability: str = "UNKNOWN"
    trend: str = "UNKNOWN"
    n_days: int = 0
    at_squeeze_ceiling: bool = False
    self_excitation: AcfSelfExcitation = AcfSelfExcitation()


class ReynoldsData(BaseModel):
    number: float
    number_beta_adj: float
    beta_adj_factor: float
    regime: str
    speculative_gamma: float
    dealer_gamma: float
    call_put_ratio: float
    atm_iv: float


class PhaseData(BaseModel):
    regime: str
    pct_amplified: float
    distance_to_transition: float
    warning: Optional[str] = None


class WallData(BaseModel):
    strike: float
    oi: int


class BiasData(BaseModel):
    direction: str
    action: str
    style: str
    description: str
    strength: str


class WallBreakData(BaseModel):
    probability: float
    confidence: str
    explanation: str
    re_says: str
    acf_says: str
    sei_says: str
    gamma_asymmetry: float
    collision_prob_call_wall: float
    collision_prob_put_wall: float
    beta_adj_factor: float
    re_beta_adj: float


class PositionData(BaseModel):
    name: str
    type: str
    edge_type: str
    action: str
    option_type: str
    strike: float
    dte_guidance: str = ""
    sizing: str = ""
    kelly_size: str = ""
    target: str = ""
    stop: str = ""
    edge: str = ""
    confidence: str = ""


class LevelAction(BaseModel):
    level: float
    label: str
    type: str
    distance_pct: float
    side: str
    expectation: str
    action: str
    watch_for: str = ""
    collision_prob: float = 0
    collision_label: str = ""


class TechContext(BaseModel):
    confirms_thesis: bool = False
    conflicts_thesis: bool = False
    trend_label: str = "UNKNOWN"
    trend_desc: str = ""
    tech_bias: str = "NEUTRAL"
    ma_alignment: str = "UNKNOWN"
    rs_label: str = "UNKNOWN"
    rs_desc: str = ""
    atr_pct: float = 0
    vwap: str = "N/A"
    vwap_desc: str = ""
    vwap_level: Optional[float] = None
    beta: float = 1.0
    beta_adj_factor: float = 1.0
    re_beta_adj: float = 0
    entropy_regime: str = "DISPERSED"
    sei: float = 0
    sei_regime: str = "NONE"


class DirectionalData(BaseModel):
    thesis: str
    thesis_label: str
    bias: BiasData
    positions: list[PositionData] = []
    level_actions: list[LevelAction] = []
    wall_break: WallBreakData
    avoid: list[str] = []
    iv_context: str = "N/A"
    atm_iv: float = 0
    tech_context: TechContext = TechContext()


class IvHvData(BaseModel):
    atm_iv: float
    hv_10d: float = 0
    hv_20d: float = 0
    hv_30d: float = 0
    hv_60d: float = 0
    hv_used: float = 0
    hv_window: str = ""
    iv_hv_ratio: float = 1.0
    iv_percentile_proxy: int = 50
    context: str = "N/A"
    label: str = ""


class SkewData(BaseModel):
    otm_put_iv: float = 0
    otm_call_iv: float = 0
    atm_iv: float = 0
    risk_reversal: float = 0
    skew_norm: float = 0
    regime: str = "UNKNOWN"
    description: str = ""
    trade_implication: str = ""


class VrpData(BaseModel):
    vrp_raw: float = 0
    vrp_gex_adjusted: float = 0
    gex_implied_hv: float = 0
    gex_vol_mult: float = 1.0
    hv_20d: float = 0
    atm_iv: float = 0
    daily_vrp_drag: float = 0
    context: str = "N/A"
    label: str = ""


class VolEdgeData(BaseModel):
    score: int = 0
    verdict: str = "NEUTRAL_VOL"
    label: str = ""
    factors: list[str] = []


class VolAnalysisData(BaseModel):
    iv_hv: IvHvData
    skew: SkewData
    term_structure: dict = {}
    vrp: VrpData = VrpData()
    vol_edge: VolEdgeData = VolEdgeData()


class StraddleScore(BaseModel):
    total: float
    regime: float
    iv: float
    catalyst: float
    structural: float
    vrp_drag: float = 0


class StraddleAnalysisData(BaseModel):
    straddle: dict
    strangle: dict
    iv_vs_rv: dict
    atr_context: dict
    move_probability: dict
    theta_schedule: dict
    pnl_scenarios: list = []
    score: StraddleScore
    vrp: dict = {}
    verdict: str
    verdict_label: str
    reasoning: list[str] = []
    warnings: list[str] = []
    suggested_dte: str = ""
    suggested_sizing: str = ""


class CollisionTime(BaseModel):
    level_label: str
    level_price: float
    distance: float
    distance_pct: float
    expected_days_raw: float
    expected_days_adj: float
    regime_mult: float
    prob_within_dte: float
    urgency: str
    side: str


class DistanceEntry(BaseModel):
    value: float
    distance: float
    distance_pct: float
    side: str


class GexProfileData(BaseModel):
    total_gex: float
    total_call_gex: float
    total_put_gex: float
    total_charm: float
    total_vanna: float
    by_strike: list = []
    entropy: dict = {}


# ── Top-Level Response ───────────────────────────────────────────────────────


class DealerMapResponse(BaseModel):
    """Full dealer positioning map + directional thesis."""
    ticker: str
    spot: float
    expiration: str
    dte: int
    timestamp: str

    gex_regime: str
    gex_regime_label: str

    acf_regime: str
    acf_data: AcfData

    reynolds: ReynoldsData
    phase: PhaseData

    channel: dict
    channel_strategy: dict

    directional: DirectionalData

    straddle_analysis: StraddleAnalysisData
    expiry_scan: dict = {}
    collision_times: list[CollisionTime] = []
    vol_analysis: VolAnalysisData

    technicals: dict = {}

    key_levels: dict = {}
    distances: dict = {}
    gex_profile: GexProfileData
    max_pain_profile: dict = {}
    available_expirations: list = []

    model_config = ConfigDict(extra="allow")
