"""Domain schemas shared by the agent, policy layer, DB and UI."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Category(str, Enum):
    BILLING = "Billing & Autopay Dispute"
    GATE = "Gate Access & Lockout"
    TRANSFER = "Unit Transfer & Reservation Change"
    DAMAGE = "Damage & Insurance Claim"
    DELINQUENCY = "Delinquency & Auction Notice"


class Signal(str, Enum):
    SAFETY_RISK = "safety_risk"                       # health/safety hazard (mold, fire, flooding)
    ACTIVE_DAMAGE = "active_property_damage"          # damage happening now / ongoing
    MINOR_DAMAGE = "minor_damage_reported"            # small, non-progressing damage
    LOCKED_OUT = "locked_out"                         # tenant cannot access unit/facility now
    TIME_CRITICAL = "time_critical_deadline"          # hard deadline within ~48h
    REPEAT_ISSUE = "repeat_unresolved_issue"          # previously reported, still unresolved
    FINANCIAL_IMMINENT = "financial_impact_imminent"  # money leaves account imminently
    LIEN_AUCTION_RISK = "lien_or_auction_risk"        # actual lien/auction/delinquency exposure
    BILLING_DISCREPANCY = "billing_discrepancy_claimed"
    NOT_URGENT = "explicitly_not_urgent"              # tenant says no rush / whenever
    INFORMATIONAL = "informational_only"              # question / document request, no problem


class ClaimKind(str, Enum):
    OVERCHARGE = "overcharge"
    UNDERCREDIT = "missing_credit_or_refund"
    DUPLICATE_CHARGE = "duplicate_charge"
    GOODWILL = "goodwill_or_compensation_request"
    NONE = "none"


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    urgency_signals: list[Signal] = []
    language: str = "en"
    claim_kind: ClaimKind = ClaimKind.NONE
    claimed_amount: Optional[float] = None
    claim_is_specific: bool = False
    summary: str
    reasoning: str


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class Team(str, Enum):
    BILLING = "Billing Team"
    ACCESS = "Access Control"
    RESERVATIONS = "Reservations"
    CLAIMS = "Claims"
    COLLECTIONS = "Collections"
    FACILITIES_EMERGENCY = "Facilities Emergency Response"
    HUMAN_TRIAGE = "Human Triage"


class RequestStatus(str, Enum):
    NEW = "new"
    OPEN = "open"
    PROCESSING = "processing"
    AWAITING_CUSTOMER = "awaiting_customer"
    ACKNOWLEDGED = "acknowledged"
    ROUTED = "routed"
    RESOLVED = "resolved"
    CLOSED_DUPLICATE = "closed_duplicate"
    CLOSED = "closed"
    NEEDS_REVIEW = "needs_review"


class ActionType(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    ROUTE = "route"
    CLOSE_DUPLICATE = "close_duplicate"
    COURTESY_CREDIT = "courtesy_credit"
    FALLBACK_ROUTE = "fallback_route"
