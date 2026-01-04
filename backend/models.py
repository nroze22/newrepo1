from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ConsultantMode(str, Enum):
    BUSINESS = "business"
    DESIGN = "design"
    CODE = "code"
    LEGAL = "legal"
    MEDICAL = "medical"
    REAL_ESTATE = "real_estate"
    MARKETING = "marketing"
    CUSTOM = "custom"


class AnalysisRequest(BaseModel):
    session_id: str
    frame_data: str  # Base64 encoded image
    audio_transcript: Optional[str] = None
    screen_share_data: Optional[str] = None
    consultant_mode: ConsultantMode
    custom_instructions: Optional[str] = None


class AnalysisResponse(BaseModel):
    session_id: str
    timestamp: datetime
    insights: List[str]
    recommendations: List[str]
    key_findings: List[str]
    risk_factors: Optional[List[str]] = None
    opportunities: Optional[List[str]] = None
    action_items: List[Dict[str, Any]]
    confidence_score: float
    detailed_analysis: str


class SessionCreate(BaseModel):
    user_id: str
    consultant_mode: ConsultantMode
    session_title: Optional[str] = None
    custom_instructions: Optional[str] = None


class Session(BaseModel):
    session_id: str
    user_id: str
    consultant_mode: ConsultantMode
    created_at: datetime
    status: str = "active"  # active, completed, analyzing
    total_frames_analyzed: int = 0
    duration_seconds: int = 0
    session_title: Optional[str] = None


class WebRTCSignal(BaseModel):
    session_id: str
    sender_id: str
    signal_type: str  # offer, answer, ice-candidate
    signal_data: Dict[str, Any]


class UsageMetrics(BaseModel):
    user_id: str
    session_count: int
    total_minutes: float
    total_cost: float
    subscription_tier: str
