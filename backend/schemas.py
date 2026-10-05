# backend/schemas.py
# UPDATED: Added status update and status history schemas.

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


ApplicationStatus = Literal[
    "Applied",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Unknown",
]


class ApplicationCreate(BaseModel):
    """
    Data accepted when creating a job application.
    """

    company_name: str
    job_title: str
    location: Optional[str] = None
    job_link: Optional[str] = None
    personal_notes: Optional[str] = None


class ApplicationResponse(BaseModel):
    """
    Job application data returned to the frontend.
    """

    id: int
    company_name: str
    job_title: str
    date_applied: date
    location: Optional[str] = None
    job_link: Optional[str] = None
    personal_notes: Optional[str] = None
    status: str
    user_id: int

    model_config = ConfigDict(from_attributes=True)


class ApplicationStatusUpdate(BaseModel):
    """
    Data accepted when manually changing an application status.
    """

    status: ApplicationStatus
    reason: Optional[str] = None


class ApplicationStatusHistoryResponse(BaseModel):
    """
    One recorded application status event.
    """

    id: int
    application_id: int
    old_status: Optional[str] = None
    new_status: str
    source: str
    reason: Optional[str] = None
    confidence: Optional[float] = None
    email_message_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# NEW: Email content submitted for manual analysis.
class EmailAnalysisRequest(BaseModel):
    sender: str
    subject: str
    body: str


# NEW: Result returned by the manual email analyzer.
class EmailAnalysisResponse(BaseModel):
    is_job_related: bool
    suggested_status: ApplicationStatus
    confidence: float
    reason: str

# NEW: Validated structured output returned by the AI.
class AIEmailClassification(BaseModel):
    is_job_related: bool
    suggested_status: ApplicationStatus
    confidence: float
    reason: str
    evidence: str
    event_is_current: bool


# NEW: Complete hybrid rule-and-AI response.
class HybridEmailAnalysisResponse(BaseModel):
    is_job_related: bool
    suggested_status: ApplicationStatus
    confidence: float
    reason: str

    analysis_source: Literal["rule", "ai"]
    ai_was_used: bool

# NEW: One possible application match.
class ApplicationMatchCandidate(BaseModel):
    application_id: int
    company_name: str
    job_title: str
    current_status: str
    match_score: int
    match_reasons: list[str]

# NEW: Combined email analysis and application matching result.
# UPDATED: Combined classification and application matching result.
class EmailApplicationMatchResponse(BaseModel):
    is_job_related: bool
    suggested_status: ApplicationStatus
    classification_confidence: float
    classification_reason: str

    # NEW: Shows whether rules or AI classified the email.
    analysis_source: Literal["rule", "ai"]
    ai_was_used: bool

    matched_application: Optional[
        ApplicationMatchCandidate
    ] = None

    alternative_matches: list[
        ApplicationMatchCandidate
    ]

    requires_confirmation: bool

# NEW: Explicitly confirmed email-based status change.
class ConfirmedEmailStatusUpdate(BaseModel):
    status: ApplicationStatus
    analysis_source: Literal["rule", "ai"]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str
    confirmed: bool


# NEW: Result returned after applying a confirmed status.
class ConfirmedEmailStatusUpdateResponse(BaseModel):
    message: str
    application: ApplicationResponse
    history_event: ApplicationStatusHistoryResponse

# NEW: Google authorization URL returned to the frontend.
class GmailConnectResponse(BaseModel):
    authorization_url: str


# NEW: Current Gmail connection state.
class GmailConnectionStatusResponse(BaseModel):
    connected: bool
    gmail_email: Optional[EmailStr] = None
    automatic_monitoring_enabled: bool = False
    monitoring_active: bool = False
    watch_expiration: Optional[datetime] = None


# NEW: Result returned after starting Gmail monitoring.
class GmailMonitoringResponse(BaseModel):
    message: str
    automatic_monitoring_enabled: bool
    gmail_history_id: str
    watch_expiration: datetime


# Result returned after disabling Gmail monitoring while staying connected.
class GmailMonitoringStopResponse(BaseModel):
    message: str
    automatic_monitoring_enabled: bool
    monitoring_active: bool

# NEW: Pub/Sub message wrapped inside a push request.
class GmailPubSubMessage(BaseModel):
    data: str
    messageId: Optional[str] = None
    publishTime: Optional[str] = None


# NEW: JSON body sent by a Pub/Sub push subscription.
class GmailPubSubPushEnvelope(BaseModel):
    message: GmailPubSubMessage
    subscription: Optional[str] = None

# NEW: Result returned after disconnecting Gmail.
class GmailDisconnectResponse(BaseModel):
    message: str

# NEW: Limited Gmail message metadata returned during preview testing.
class GmailMessagePreview(BaseModel):
    gmail_message_id: str
    gmail_thread_id: str
    sender: str
    subject: str
    received_at: str
    snippet: str


# NEW: Result returned when previewing recent Gmail messages.
class GmailMessagePreviewResponse(BaseModel):
    gmail_email: EmailStr
    message_count: int
    messages: list[GmailMessagePreview]

# NEW: One saved suggestion created from a Gmail message.
class GmailSyncSuggestion(BaseModel):
    id: int
    gmail_message_id: str
    gmail_thread_id: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None
    received_at: Optional[str] = None

    is_job_related: bool
    suggested_status: ApplicationStatus
    confidence: float
    reason: str
    analysis_source: Literal["rule", "ai"]

    application_id: Optional[int] = None
    match_score: Optional[int] = None
    requires_confirmation: bool
    review_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# NEW: Result returned by one manual Gmail sync.
class GmailSyncResponse(BaseModel):
    gmail_email: EmailStr
    messages_checked: int
    duplicates_skipped: int
    irrelevant_messages: int
    suggestions_created: int
    suggestions: list[GmailSyncSuggestion]


# Optional application selected by the user while confirming a suggestion.
class GmailSuggestionConfirmRequest(BaseModel):
    application_id: Optional[int] = Field(default=None, gt=0)


# NEW: Result returned after reviewing a Gmail suggestion.
class GmailSuggestionReviewResponse(BaseModel):
    message: str
    suggestion: GmailSyncSuggestion
    application: Optional[ApplicationResponse] = None
    history_event: Optional[
        ApplicationStatusHistoryResponse
    ] = None

class UserCreate(BaseModel):
    """
    Data accepted during account registration.
    """

    email: EmailStr
    password: str


class UserLogin(BaseModel):
    """
    Data accepted during login.
    """

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """
    Safe user data returned by the API.
    """

    id: int
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """
    JWT response returned after a successful login.
    """

    access_token: str
    token_type: str
    user: UserResponse
