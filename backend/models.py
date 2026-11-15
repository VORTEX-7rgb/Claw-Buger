from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class BugEvidence(BaseModel):
    bug_id: str
    title: str
    category: str  # "Layout Collision" | "Race Condition" | "State Loss" | "Form Validation" | "Accessibility"
    severity: str  # "P0 (Critical)" | "P1 (High)" | "P2 (Medium)"
    reproduction_rate: str  # e.g., "3/3 Reproduced"
    affected_elements: List[str] = Field(default_factory=list)
    evidence_telemetry: Dict[str, Any] = Field(default_factory=dict)
    repro_steps: List[str] = Field(default_factory=list)
    spec_code: Optional[str] = None
    patch_code: Optional[str] = None

class LayoutCollision(BaseModel):
    floating_tag: str
    floating_selector: str
    blocked_tag: str
    blocked_selector: str
    overlap_w: float
    overlap_h: float
    overlap_area: float

class NetworkError(BaseModel):
    url: str
    status: Optional[int] = None
    failure_text: Optional[str] = None

class TelemetryPayload(BaseModel):
    url: str
    viewport: Dict[str, int]
    horizontal_overflow: bool = False
    scroll_width: int = 0
    window_width: int = 0
    network_errors: List[NetworkError] = Field(default_factory=list)
    console_errors: List[str] = Field(default_factory=list)
    layout_collisions: List[LayoutCollision] = Field(default_factory=list)
    accessibility_issues: List[Dict[str, Any]] = Field(default_factory=list)
    aria_snapshot: Optional[str] = None

class AuditRequest(BaseModel):
    url: str = "http://localhost:3001"

class WSMessage(BaseModel):
    type: str  # "frame" | "log" | "repro_tick" | "bug_found" | "audit_complete" | "error"
    data: Any
