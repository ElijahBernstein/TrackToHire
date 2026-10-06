# backend/main.py
# UPDATED: Added application status history endpoints.
from fastapi.responses import HTMLResponse
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error

import base64
import binascii
import json
import os
import re
import secrets
from datetime import date, datetime, timezone

import bcrypt
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from gmail_oauth import (
    GMAIL_SCOPES,
    create_gmail_oauth_state,
    create_google_oauth_flow,
    decode_gmail_oauth_state,
)
from gmail_service import (
    get_gmail_history_previews,
    get_recent_gmail_messages_for_processing,
    get_recent_gmail_previews,
    start_gmail_watch,
)
from token_encryption import encrypt_refresh_token
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import models
import schemas
from ai_service import classify_email_with_ai
from auth import create_access_token, get_current_user
from database import Base, SessionLocal, engine


# --- DATABASE INITIALIZATION ---

# Creates missing tables without deleting existing data.
Base.metadata.create_all(bind=engine)


app = FastAPI(title="TrackToHire API")


# --- CORS CONFIGURATION ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tracktohire.app",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- DATABASE SESSION ---

def get_db():
    """
    Creates one SQLAlchemy session for an API request.
    """

    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

# --- MANUAL EMAIL ANALYSIS HELPERS ---

def analyze_recruiting_email(
    sender: str,
    subject: str,
    body: str,
) -> dict:
    """
    Uses simple rules to classify an email before AI is introduced.
    """

    combined_text = " ".join(
        [
            sender,
            subject,
            body,
        ]
    ).lower()

    rejection_phrases = [
        "not moving forward",
        "move forward with other candidates",
        "moving forward with other candidates",
        "decided to pursue other candidates",
        "decided to proceed with other candidates",
        "unable to offer you",
        "will not be moving forward",
        "regret to inform you",
        "position has been filled",
    ]

    offer_phrases = [
        "pleased to offer",
        "offer of employment",
        "employment offer",
        "job offer",
        "offer letter",
        "extend an offer",
    ]

    interview_phrases = [
        "schedule an interview",
        "invite you to interview",
        "interview invitation",
        "meet with the hiring manager",
        "phone interview",
        "video interview",
        "technical interview",
        "next round of interviews",
        "next step in the interview process",
    ]

    assessment_phrases = [
        "complete an assessment",
        "online assessment",
        "coding assessment",
        "technical assessment",
        "skills assessment",
        "coding challenge",
        "take-home assignment",
        "complete this test",
    ]

    applied_phrases = [
        "application received",
        "received your application",
        "thank you for applying",
        "application confirmation",
        "successfully submitted",
        "under review",
    ]

    job_related_phrases = [
        "application",
        "candidate",
        "position",
        "job",
        "role",
        "recruiter",
        "hiring",
        "interview",
        "assessment",
        "offer",
    ]

    def contains_any(phrases: list[str]) -> bool:
        return any(
            phrase in combined_text
            for phrase in phrases
        )

    if contains_any(rejection_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Rejected",
            "confidence": 0.98,
            "reason": (
                "The email contains language indicating that "
                "the company is not continuing with the application."
            ),
        }

    if contains_any(offer_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Offer",
            "confidence": 0.98,
            "reason": (
                "The email contains language indicating that "
                "an employment offer was made."
            ),
        }

    if contains_any(interview_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Interview",
            "confidence": 0.95,
            "reason": (
                "The email contains language about scheduling "
                "or attending an interview."
            ),
        }

    if contains_any(assessment_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Assessment",
            "confidence": 0.95,
            "reason": (
                "The email asks the applicant to complete "
                "an assessment, challenge, test, or assignment."
            ),
        }

    if contains_any(applied_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Applied",
            "confidence": 0.90,
            "reason": (
                "The email appears to confirm that an "
                "application was received or is under review."
            ),
        }

    if contains_any(job_related_phrases):
        return {
            "is_job_related": True,
            "suggested_status": "Unknown",
            "confidence": 0.55,
            "reason": (
                "The email appears job-related, but the rules "
                "could not determine a clear application status."
            ),
        }

    return {
        "is_job_related": False,
        "suggested_status": "Unknown",
        "confidence": 0.10,
        "reason": (
            "The email does not contain enough recruiting or "
            "job-application language to classify it."
        ),
    }

# --- HYBRID CLASSIFICATION HELPER ---

def get_hybrid_email_analysis(
    sender: str,
    subject: str,
    body: str,
) -> dict:
    """
    Uses rules for broad job-email detection and AI for the final status.

    A keyword match must never make the final status decision. Recruiting
    emails often mention an earlier stage while announcing a newer outcome.
    """

    rule_analysis = analyze_recruiting_email(
        sender=sender,
        subject=subject,
        body=body,
    )

    if not rule_analysis["is_job_related"]:
        return {
            "is_job_related": False,
            "suggested_status": "Unknown",
            "confidence": rule_analysis["confidence"],
            "reason": rule_analysis["reason"],
            "analysis_source": "rule",
            "ai_was_used": False,
        }

    ai_analysis = classify_email_with_ai(
        sender=sender,
        subject=subject,
        body=body,
    )

    return {
        "is_job_related": ai_analysis.is_job_related,
        "suggested_status": ai_analysis.suggested_status,
        "confidence": ai_analysis.confidence,
        "reason": ai_analysis.reason,
        "analysis_source": "ai",
        "ai_was_used": True,
    }

# --- APPLICATION MATCHING HELPERS ---

def normalize_match_text(value: str) -> str:
    """
    Converts text into a simplified form for matching.
    """

    lowered_value = value.lower()

    normalized_value = re.sub(
        r"[^a-z0-9]+",
        " ",
        lowered_value,
    )

    return " ".join(normalized_value.split())


def extract_sender_domain(sender: str) -> str:
    """
    Extracts the domain from an email sender string.
    """

    match = re.search(
        r"@([a-zA-Z0-9.-]+)",
        sender,
    )

    if not match:
        return ""

    return normalize_match_text(match.group(1))


def get_meaningful_words(value: str) -> set[str]:
    """
    Removes short and common words from matching text.
    """

    ignored_words = {
        "a",
        "an",
        "and",
        "at",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "job",
        "role",
        "position",
        "application",
        "careers",
        "company",
        "corporation",
        "group",
        "inc",
        "llc",
        "recruiting",
        "team",
    }

    normalized_value = normalize_match_text(value)

    return {
        word
        for word in normalized_value.split()
        if len(word) >= 3 and word not in ignored_words
    }


def contains_normalized_phrase(text: str, phrase: str) -> bool:
    """Matches complete normalized words instead of loose substrings."""

    if not text or not phrase:
        return False

    return f" {phrase} " in f" {text} "


def score_application_match(
    application: models.DBApplication,
    sender: str,
    subject: str,
    body: str,
) -> dict:
    """
    Scores how strongly an email matches one saved application.
    """

    combined_email_text = normalize_match_text(
        f"{sender} {subject} {body}"
    )

    sender_domain = extract_sender_domain(sender)

    company_name = normalize_match_text(
        application.company_name
    )

    job_title = normalize_match_text(
        application.job_title
    )

    company_words = get_meaningful_words(
        application.company_name
    )

    title_words = get_meaningful_words(
        application.job_title
    )

    email_words = get_meaningful_words(
        combined_email_text
    )

    match_score = 0
    match_reasons = []

    # Require a complete company phrase containing at least one meaningful
    # word. This prevents short names such as "AI" from matching "email".
    if (
        company_words
        and contains_normalized_phrase(
            combined_email_text,
            company_name,
        )
    ):
        match_score += 45
        match_reasons.append(
            "The company name appears in the email."
        )
    else:
        matching_company_words = (
            company_words & email_words
        )

        if matching_company_words:
            company_word_score = min(
                len(matching_company_words) * 12,
                30,
            )

            match_score += company_word_score
            match_reasons.append(
                "Part of the company name appears in the email."
            )

    # Sender-domain company match.
    domain_company_words = (
        company_words
        & get_meaningful_words(sender_domain)
    )

    if domain_company_words:
        match_score += 25
        match_reasons.append(
            "The sender domain resembles the company name."
        )

    # Exact job-title match.
    if (
        title_words
        and contains_normalized_phrase(
            combined_email_text,
            job_title,
        )
    ):
        match_score += 40
        match_reasons.append(
            "The complete job title appears in the email."
        )
    else:
        matching_title_words = title_words & email_words

        if title_words:
            title_match_ratio = (
                len(matching_title_words)
                / len(title_words)
            )
        else:
            title_match_ratio = 0

        if title_match_ratio >= 0.75:
            match_score += 30
            match_reasons.append(
                "Most words from the job title appear in the email."
            )
        elif title_match_ratio >= 0.40:
            match_score += 18
            match_reasons.append(
                "Some words from the job title appear in the email."
            )

    return {
        "application_id": application.id,
        "company_name": application.company_name,
        "job_title": application.job_title,
        "current_status": application.status,
        "match_score": min(match_score, 100),
        "match_reasons": match_reasons,
    }

# --- GMAIL SYNC MATCHING HELPER ---

def find_best_application_match(
    user_applications: list[models.DBApplication],
    sender: str,
    subject: str,
    body: str,
) -> tuple[dict | None, bool]:
    """
    Finds the strongest application match for one Gmail message.

    Returns:
    - the best usable match, or None
    - whether user confirmation is required
    """

    scored_matches = [
        score_application_match(
            application=application,
            sender=sender,
            subject=subject,
            body=body,
        )
        for application in user_applications
    ]

    scored_matches.sort(
        key=lambda match: match["match_score"],
        reverse=True,
    )

    useful_matches = [
        match
        for match in scored_matches
        if match["match_score"] > 0
    ]

    if not useful_matches:
        return None, True

    best_match = useful_matches[0]

    second_best_score = (
        useful_matches[1]["match_score"]
        if len(useful_matches) > 1
        else 0
    )

    score_difference = (
        best_match["match_score"]
        - second_best_score
    )

    if (
        best_match["match_score"] >= 70
        and score_difference >= 15
    ):
        return best_match, False

    # A single weak signal must not attach an email to an application.
    # The user can choose the application later when evidence is insufficient.
    return None, True

# --- AUTHENTICATION ENDPOINTS ---

@app.post(
    "/api/v1/auth/signup",
    response_model=schemas.UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def signup(
    user_data: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    """
    Creates a new TrackToHire account.
    """

    normalized_email = user_data.email.lower().strip()

    existing_user = (
        db.query(models.DBUser)
        .filter(models.DBUser.email == normalized_email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    hashed_password = bcrypt.hashpw(
        user_data.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    new_user = models.DBUser(
        email=normalized_email,
        hashed_password=hashed_password,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@app.post(
    "/api/v1/auth/login",
    response_model=schemas.TokenResponse,
)
def login(
    user_data: schemas.UserLogin,
    db: Session = Depends(get_db),
):
    """
    Verifies credentials and returns a signed JWT.
    """

    normalized_email = user_data.email.lower().strip()

    user = (
        db.query(models.DBUser)
        .filter(models.DBUser.email == normalized_email)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    password_is_correct = bcrypt.checkpw(
        user_data.password.encode("utf-8"),
        user.hashed_password.encode("utf-8"),
    )

    if not password_is_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    access_token = create_access_token(user_id=user.id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
    }


@app.get(
    "/api/v1/auth/me",
    response_model=schemas.UserResponse,
)
def get_logged_in_user(
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns the user represented by the supplied JWT.
    """

    return current_user

# --- GMAIL OAUTH ENDPOINTS ---

@app.get(
    "/api/v1/gmail/connect",
    response_model=schemas.GmailConnectResponse,
)
def connect_gmail(
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Creates a Google authorization URL for the logged-in user.
    """

    try:
        oauth_state = create_gmail_oauth_state(
            user_id=current_user.id
        )

        flow = create_google_oauth_flow(
            state=oauth_state
        )

        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            login_hint=current_user.email,
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        )

    return {
        "authorization_url": authorization_url,
    }


@app.get(
    "/api/v1/gmail/oauth/callback",
    response_class=HTMLResponse,
)
def gmail_oauth_callback(
    request: Request,
    state_value: str = Query(alias="state"),
    code: str | None = Query(default=None),
    oauth_error: str | None = Query(
        default=None,
        alias="error",
    ),
    db: Session = Depends(get_db),
):
    """
    Receives Google's OAuth callback and stores an encrypted
    refresh token for the correct TrackToHire user.
    """

    if oauth_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Google authorization was not completed: "
                f"{oauth_error}"
            ),
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return an authorization code.",
        )

    try:
        user_id = decode_gmail_oauth_state(
            state_value
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )

    user = (
        db.query(models.DBUser)
        .filter(models.DBUser.id == user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TrackToHire user not found.",
        )

    existing_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id == user.id
        )
        .first()
    )

    try:
        flow = create_google_oauth_flow(
            state=state_value
        )

        # Exchanges Google's temporary code for OAuth credentials.
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Uses the temporary access token to identify the Gmail account.
        gmail_service = build(
            "gmail",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )

        gmail_profile = (
            gmail_service.users()
            .getProfile(userId="me")
            .execute()
        )

        gmail_email = gmail_profile["emailAddress"]

    except OAuth2Error as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google could not complete authorization.",
        ) from error
    except HttpError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Gmail API could not read the Gmail profile.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        )

    refresh_token = credentials.refresh_token

    if not refresh_token and not existing_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Google did not return a refresh token. "
                "Remove the app from your Google Account permissions "
                "and connect again."
            ),
        )

    encrypted_refresh_token = None

    if refresh_token:
        try:
            encrypted_refresh_token = encrypt_refresh_token(
                refresh_token
            )
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(error),
            )

    stored_scopes = " ".join(
        credentials.scopes or GMAIL_SCOPES
    )

    if existing_connection:
        existing_connection.gmail_email = gmail_email
        existing_connection.scopes = stored_scopes

        # Do not erase a valid saved token if Google omitted a new one.
        if encrypted_refresh_token:
            existing_connection.encrypted_refresh_token = (
                encrypted_refresh_token
            )

        gmail_connection = existing_connection
    else:
        gmail_connection = models.DBGmailConnection(
            user_id=user.id,
            gmail_email=gmail_email,
            encrypted_refresh_token=encrypted_refresh_token,
            scopes=stored_scopes,
        )

        db.add(gmail_connection)

    db.commit()

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Gmail Connected</title>
        </head>
        <body style="font-family: sans-serif; padding: 40px;">
            <h1>Gmail connected successfully</h1>
            <p>{gmail_email} is now connected to TrackToHire.</p>
            <p>You may close this tab and return to the application.</p>
        </body>
        </html>
        """,
        status_code=status.HTTP_200_OK,
    )


@app.get(
    "/api/v1/gmail/status",
    response_model=schemas.GmailConnectionStatusResponse,
)
def get_gmail_connection_status(
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns the logged-in user's Gmail connection and monitoring state.
    """

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        return {
            "connected": False,
            "gmail_email": None,
            "automatic_monitoring_enabled": False,
            "monitoring_active": False,
            "watch_expiration": None,
        }

    watch_expiration = gmail_connection.watch_expiration

    # PostgreSQL normally returns this as timezone-aware, but this
    # safely handles older timezone-naive values too.
    if (
        watch_expiration is not None
        and watch_expiration.tzinfo is None
    ):
        watch_expiration = watch_expiration.replace(
            tzinfo=timezone.utc
        )

    monitoring_active = bool(
        gmail_connection.automatic_monitoring_enabled
        and watch_expiration is not None
        and watch_expiration > datetime.now(timezone.utc)
    )

    return {
        "connected": True,
        "gmail_email": gmail_connection.gmail_email,
        "automatic_monitoring_enabled": (
            gmail_connection.automatic_monitoring_enabled
        ),
        "monitoring_active": monitoring_active,
        "watch_expiration": watch_expiration,
    }

# NEW: Starts Gmail push monitoring for the authenticated user.
@app.post(
    "/api/v1/gmail/monitoring/start",
    response_model=schemas.GmailMonitoringResponse,
)
def start_gmail_monitoring(
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Starts or renews Gmail push notifications for the authenticated
    user's connected Gmail account.

    This endpoint does not process emails or update applications.
    """

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Connect Gmail before enabling automatic monitoring."
            ),
        )

    try:
        watch_result = start_gmail_watch(
            gmail_connection=gmail_connection
        )

        expiration_milliseconds = int(
            watch_result["expiration"]
        )

        watch_expiration = datetime.fromtimestamp(
            expiration_milliseconds / 1000,
            tz=timezone.utc,
        )

    except (RuntimeError, ValueError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error

    gmail_connection.automatic_monitoring_enabled = True
    gmail_connection.gmail_history_id = (
        watch_result["history_id"]
    )
    gmail_connection.watch_expiration = watch_expiration

    db.commit()
    db.refresh(gmail_connection)

    return {
        "message": "Automatic Gmail monitoring started.",
        "automatic_monitoring_enabled": (
            gmail_connection.automatic_monitoring_enabled
        ),
        "gmail_history_id": (
            gmail_connection.gmail_history_id
        ),
        "watch_expiration": (
            gmail_connection.watch_expiration
        ),
    }


@app.post(
    "/api/v1/gmail/monitoring/stop",
    response_model=schemas.GmailMonitoringStopResponse,
)
def stop_gmail_monitoring(
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """Disables push processing without disconnecting Gmail."""

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Gmail before changing monitoring settings.",
        )

    # Pub/Sub deliveries may continue until Google's existing watch expires,
    # but the webhook and worker ignore them as soon as this flag is false.
    gmail_connection.automatic_monitoring_enabled = False
    gmail_connection.watch_expiration = None

    db.commit()
    db.refresh(gmail_connection)

    return {
        "message": "Automatic Gmail monitoring disabled.",
        "automatic_monitoring_enabled": False,
        "monitoring_active": False,
    }

# NEW: Saves Gmail mailbox-change notifications as durable jobs.
@app.post(
    "/api/v1/gmail/pubsub/push",
    status_code=status.HTTP_204_NO_CONTENT,
)
def receive_gmail_pubsub_notification(
    envelope: schemas.GmailPubSubPushEnvelope,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Saves one small, deduplicated processing job and quickly
    acknowledges the Pub/Sub notification.
    """

    expected_token = os.getenv(
        "GMAIL_PUBSUB_VERIFICATION_TOKEN"
    )

    if (
        not expected_token
        or not secrets.compare_digest(
            token,
            expected_token,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Pub/Sub verification token.",
        )

    try:
        decoded_data = base64.urlsafe_b64decode(
            envelope.message.data + "=="
        ).decode("utf-8")

        notification_data = json.loads(decoded_data)

    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Pub/Sub message data.",
        ) from error

    gmail_email = (
        notification_data.get("emailAddress", "")
        .strip()
        .lower()
    )

    history_id = str(
        notification_data.get("historyId", "")
    ).strip()

    if not gmail_email or not history_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incomplete Gmail notification.",
        )

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.gmail_email
            == gmail_email,
            models.DBGmailConnection
            .automatic_monitoring_enabled
            .is_(True),
        )
        .first()
    )

    if not gmail_connection:
        return Response(
            status_code=status.HTTP_204_NO_CONTENT
        )

    # PostgreSQL enforces the same deduplication during concurrent retries.
    insert_job = (
        insert(models.DBGmailProcessingJob)
        .values(
            gmail_connection_id=gmail_connection.id,
            history_id=history_id,
            pubsub_message_id=envelope.message.messageId,
            status="pending",
            attempt_count=0,
        )
        .on_conflict_do_nothing(
            constraint=(
                "uq_gmail_processing_job_connection_history"
            )
        )
    )

    db.execute(insert_job)
    db.commit()

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )
@app.get(
    "/api/v1/gmail/messages/preview",
    response_model=schemas.GmailMessagePreviewResponse,
)
def preview_recent_gmail_messages(
    max_results: int = Query(
        default=5,
        ge=1,
        le=10,
    ),
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns limited previews of recent inbox messages.

    This endpoint does not classify emails, store message content,
    or update job applications.
    """

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Connect Gmail before previewing messages."
            ),
        )

    try:
        messages = get_recent_gmail_previews(
            gmail_connection=gmail_connection,
            max_results=max_results,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        )

    return {
        "gmail_email": gmail_connection.gmail_email,
        "message_count": len(messages),
        "messages": messages,
    }

# Shared by manual sync now and the durable worker in the next phase.
def process_gmail_messages(
    db: Session,
    user_id: int,
    gmail_messages: list[dict],
) -> dict:
    """
    Classifies Gmail messages and adds new suggestion records.

    Full body text is used transiently when available. It is never written to
    a database model or included in a saved suggestion.

    This function deliberately does not commit. Its caller controls the
    transaction so a worker can later save suggestions, advance Gmail
    history, and complete its processing job atomically.
    """

    user_applications = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.user_id == user_id
        )
        .all()
    )

    messages_checked = len(gmail_messages)
    duplicates_skipped = 0
    irrelevant_messages = 0
    created_suggestions = []

    for message in gmail_messages:
        gmail_message_id = message["gmail_message_id"]

        existing_record = (
            db.query(models.DBGmailSuggestion)
            .filter(
                models.DBGmailSuggestion.user_id == user_id,
                models.DBGmailSuggestion.gmail_message_id
                == gmail_message_id,
            )
            .first()
        )

        if existing_record:
            duplicates_skipped += 1
            continue

        sender = message["sender"].strip()
        subject = message["subject"].strip()
        snippet = message.get("snippet", "").strip()
        message_text = (
            message.get("body_text")
            or snippet
        ).strip()

        # First use inexpensive rules to filter obvious unrelated mail.
        rule_analysis = analyze_recruiting_email(
            sender=sender,
            subject=subject,
            body=message_text,
        )

        if not rule_analysis["is_job_related"]:
            irrelevant_messages += 1

            # Save a minimal ignored record so this message is not
            # repeatedly analyzed during future syncs.
            processed_record = models.DBGmailSuggestion(
                user_id=user_id,
                application_id=None,
                gmail_message_id=gmail_message_id,
                gmail_thread_id=message["gmail_thread_id"],
                sender=sender or None,
                subject=subject or None,
                received_at=message["received_at"] or None,
                is_job_related=False,
                suggested_status="Unknown",
                confidence=rule_analysis["confidence"],
                reason=rule_analysis["reason"],
                analysis_source="rule",
                match_score=None,
                requires_confirmation=False,
                review_status="ignored",
            )

            db.add(processed_record)
            continue

        # Rules identify potentially relevant mail. AI determines the final
        # status using the complete message text when Gmail provides it.
        # RuntimeError is handled by the caller.
        analysis = get_hybrid_email_analysis(
            sender=sender,
            subject=subject,
            body=message_text,
        )

        # The broad rules intentionally err toward sending questionable mail
        # to AI. If AI determines it is unrelated, save only a deduplication
        # record and never expose it as a pending suggestion.
        if not analysis["is_job_related"]:
            irrelevant_messages += 1

            processed_record = models.DBGmailSuggestion(
                user_id=user_id,
                application_id=None,
                gmail_message_id=gmail_message_id,
                gmail_thread_id=message["gmail_thread_id"],
                sender=sender or None,
                subject=subject or None,
                received_at=message["received_at"] or None,
                is_job_related=False,
                suggested_status="Unknown",
                confidence=analysis["confidence"],
                reason=analysis["reason"],
                analysis_source=analysis["analysis_source"],
                match_score=None,
                requires_confirmation=False,
                review_status="ignored",
            )

            db.add(processed_record)
            continue

        best_match, match_requires_confirmation = (
            find_best_application_match(
                user_applications=user_applications,
                sender=sender,
                subject=subject,
                body=message_text,
            )
        )

        application_id = None
        match_score = None

        if best_match:
            application_id = best_match["application_id"]
            match_score = best_match["match_score"]

        requires_confirmation = True

        if (
            analysis["suggested_status"] == "Unknown"
            or analysis["confidence"] < 0.80
            or match_requires_confirmation
            or application_id is None
        ):
            requires_confirmation = True

        new_suggestion = models.DBGmailSuggestion(
            user_id=user_id,
            application_id=application_id,
            gmail_message_id=gmail_message_id,
            gmail_thread_id=message["gmail_thread_id"],
            sender=sender or None,
            subject=subject or None,
            received_at=message["received_at"] or None,
            is_job_related=analysis["is_job_related"],
            suggested_status=analysis["suggested_status"],
            confidence=analysis["confidence"],
            reason=analysis["reason"],
            analysis_source=analysis["analysis_source"],
            match_score=match_score,
            requires_confirmation=requires_confirmation,
            review_status="pending",
        )

        db.add(new_suggestion)

        # Assign its database ID before constructing an API response.
        db.flush()
        created_suggestions.append(new_suggestion)

    return {
        "messages_checked": messages_checked,
        "duplicates_skipped": duplicates_skipped,
        "irrelevant_messages": irrelevant_messages,
        "suggestions_created": len(created_suggestions),
        "suggestions": created_suggestions,
    }


# NEW: Manually analyze recent Gmail messages and create suggestions.
@app.post(
    "/api/v1/gmail/sync",
    response_model=schemas.GmailSyncResponse,
)
def manually_sync_gmail(
    max_results: int = Query(
        default=10,
        ge=1,
        le=20,
    ),
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Fetches recent Gmail messages, skips previously processed IDs,
    classifies relevant messages, matches them to owned applications,
    and stores pending suggestions.

    This endpoint never changes an application status automatically.
    """

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Gmail before running a sync.",
        )

    try:
        gmail_messages = get_recent_gmail_messages_for_processing(
            gmail_connection=gmail_connection,
            max_results=max_results,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        )

    try:
        sync_result = process_gmail_messages(
            db=db,
            user_id=current_user.id,
            gmail_messages=gmail_messages,
        )
        db.commit()
    except RuntimeError as error:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except Exception:
        db.rollback()
        raise

    for suggestion in sync_result["suggestions"]:
        db.refresh(suggestion)

    return {
        "gmail_email": gmail_connection.gmail_email,
        **sync_result,
    }

# NEW: Return saved Gmail suggestions for the logged-in user.
@app.get(
    "/api/v1/gmail/suggestions",
    response_model=list[schemas.GmailSyncSuggestion],
)
def get_gmail_suggestions(
    review_status: str = Query(
        default="pending",
        pattern="^(pending|confirmed|ignored)$",
    ),
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns Gmail suggestions belonging only to the authenticated user.
    """

    suggestions = (
        db.query(models.DBGmailSuggestion)
        .filter(
            models.DBGmailSuggestion.user_id
            == current_user.id,
            models.DBGmailSuggestion.review_status
            == review_status,
        )
        .order_by(
            models.DBGmailSuggestion.created_at.desc(),
            models.DBGmailSuggestion.id.desc(),
        )
        .all()
    )

    return suggestions

# NEW: Confirm one Gmail suggestion and apply its status.
@app.post(
    "/api/v1/gmail/suggestions/{suggestion_id}/confirm",
    response_model=schemas.GmailSuggestionReviewResponse,
)
def confirm_gmail_suggestion(
    suggestion_id: int,
    confirmation: schemas.GmailSuggestionConfirmRequest | None = None,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Confirms one owned pending Gmail suggestion and applies its
    suggested status to the matched owned application.
    """

    suggestion = (
        db.query(models.DBGmailSuggestion)
        .filter(
            models.DBGmailSuggestion.id == suggestion_id,
            models.DBGmailSuggestion.user_id
            == current_user.id,
        )
        .first()
    )

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gmail suggestion not found.",
        )

    if suggestion.review_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This Gmail suggestion was already reviewed.",
        )

    selected_application_id = suggestion.application_id

    if confirmation and confirmation.application_id is not None:
        selected_application_id = confirmation.application_id

    if selected_application_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Choose one of your applications before confirming "
                "this suggestion."
            ),
        )

    if suggestion.suggested_status == "Unknown":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown cannot be applied as a status.",
        )

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id
            == selected_application_id,
            models.DBApplication.user_id
            == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Selected application not found.",
        )

    # Save a verified manual selection before applying the suggestion.
    suggestion.application_id = application.id

    if application.status == suggestion.suggested_status:
        suggestion.review_status = "confirmed"
        db.commit()
        db.refresh(suggestion)

        return {
            "message": (
                "Suggestion confirmed. The application already "
                "had the suggested status."
            ),
            "suggestion": suggestion,
            "application": application,
            "history_event": None,
        }

    previous_status = application.status
    application.status = suggestion.suggested_status
    suggestion.review_status = "confirmed"

    # Record exactly which Gmail message caused the change.
    history_event = models.DBApplicationStatusHistory(
        application_id=application.id,
        old_status=previous_status,
        new_status=suggestion.suggested_status,
        source="gmail",
        reason=suggestion.reason,
        confidence=suggestion.confidence,
        email_message_id=suggestion.gmail_message_id,
    )

    db.add(history_event)
    db.commit()

    db.refresh(application)
    db.refresh(suggestion)
    db.refresh(history_event)

    return {
        "message": "Gmail suggestion confirmed.",
        "suggestion": suggestion,
        "application": application,
        "history_event": history_event,
    }

# NEW: Ignore one pending Gmail suggestion.
@app.post(
    "/api/v1/gmail/suggestions/{suggestion_id}/ignore",
    response_model=schemas.GmailSuggestionReviewResponse,
)
def ignore_gmail_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Marks one owned pending Gmail suggestion as ignored without
    changing an application.
    """

    suggestion = (
        db.query(models.DBGmailSuggestion)
        .filter(
            models.DBGmailSuggestion.id == suggestion_id,
            models.DBGmailSuggestion.user_id
            == current_user.id,
        )
        .first()
    )

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gmail suggestion not found.",
        )

    if suggestion.review_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This Gmail suggestion was already reviewed.",
        )

    suggestion.review_status = "ignored"

    db.commit()
    db.refresh(suggestion)

    return {
        "message": "Gmail suggestion ignored.",
        "suggestion": suggestion,
        "application": None,
        "history_event": None,
    }

@app.delete(
    "/api/v1/gmail/disconnect",
    response_model=schemas.GmailDisconnectResponse,
)
def disconnect_gmail(
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Deletes the logged-in user's stored Gmail connection.
    """

    gmail_connection = (
        db.query(models.DBGmailConnection)
        .filter(
            models.DBGmailConnection.user_id
            == current_user.id
        )
        .first()
    )

    if not gmail_connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gmail is not connected.",
        )

    db.delete(gmail_connection)
    db.commit()

    return {
        "message": "Gmail disconnected successfully.",
    }

# --- MANUAL EMAIL ANALYSIS ENDPOINT ---

@app.post(
    "/api/v1/email-analysis/test",
    response_model=schemas.EmailAnalysisResponse,
)
def test_email_analysis(
    email_data: schemas.EmailAnalysisRequest,
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Manually analyzes pasted email content.

    This endpoint does not update an application yet.
    """

    analysis = analyze_recruiting_email(
        sender=email_data.sender.strip(),
        subject=email_data.subject.strip(),
        body=email_data.body.strip(),
    )

    return analysis

# --- HYBRID RULE AND AI ANALYSIS ENDPOINT ---

@app.post(
    "/api/v1/email-analysis/hybrid",
    response_model=schemas.HybridEmailAnalysisResponse,
)
def analyze_email_with_rules_and_ai(
    email_data: schemas.EmailAnalysisRequest,
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Uses rules first and AI only for ambiguous emails.

    This endpoint does not update an application.
    """

    try:
        analysis = get_hybrid_email_analysis(
            sender=email_data.sender.strip(),
            subject=email_data.subject.strip(),
            body=email_data.body.strip(),
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )

    return analysis


# --- EMAIL TO APPLICATION MATCHING ENDPOINT ---

@app.post(
    "/api/v1/email-analysis/match",
    response_model=schemas.EmailApplicationMatchResponse,
)
def match_email_to_application(
    email_data: schemas.EmailAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Classifies an email and matches it to one of the
    authenticated user's saved applications.

    Rules run first. AI is used only when classification
    is ambiguous. This endpoint does not update the database.
    """

    sender = email_data.sender.strip()
    subject = email_data.subject.strip()
    body = email_data.body.strip()

    try:
        analysis = get_hybrid_email_analysis(
            sender=sender,
            subject=subject,
            body=body,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )

    user_applications = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.user_id
            == current_user.id
        )
        .all()
    )

    scored_matches = [
        score_application_match(
            application=application,
            sender=sender,
            subject=subject,
            body=body,
        )
        for application in user_applications
    ]

    scored_matches.sort(
        key=lambda match: match["match_score"],
        reverse=True,
    )

    useful_matches = [
        match
        for match in scored_matches
        if match["match_score"] > 0
    ]

    matched_application = None
    alternative_matches = []
    requires_confirmation = True

    if useful_matches:
        best_match = useful_matches[0]

        second_best_score = (
            useful_matches[1]["match_score"]
            if len(useful_matches) > 1
            else 0
        )

        score_difference = (
            best_match["match_score"]
            - second_best_score
        )

        if (
            best_match["match_score"] >= 70
            and score_difference >= 15
        ):
            matched_application = best_match
            requires_confirmation = False

        elif best_match["match_score"] >= 35:
            matched_application = best_match
            requires_confirmation = True

        alternative_matches = useful_matches[1:4]

    # Unclear AI classifications always require confirmation.
    if (
        analysis["suggested_status"] == "Unknown"
        or analysis["confidence"] < 0.80
    ):
        requires_confirmation = True

    return {
        "is_job_related": analysis["is_job_related"],
        "suggested_status": analysis["suggested_status"],
        "classification_confidence": analysis["confidence"],
        "classification_reason": analysis["reason"],
        "analysis_source": analysis["analysis_source"],
        "ai_was_used": analysis["ai_was_used"],
        "matched_application": matched_application,
        "alternative_matches": alternative_matches,
        "requires_confirmation": requires_confirmation,
    }

# --- CONFIRMED EMAIL STATUS UPDATE ENDPOINT ---

@app.post(
    "/api/v1/applications/{application_id}/confirm-email-status",
    response_model=schemas.ConfirmedEmailStatusUpdateResponse,
)
def confirm_email_status_update(
    application_id: int,
    update_data: schemas.ConfirmedEmailStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Applies an email-based status suggestion only after
    explicit user confirmation.
    """

    if not update_data.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The status update must be explicitly confirmed."
            ),
        )

    if update_data.status == "Unknown":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unknown cannot be applied as a confirmed "
                "application status."
            ),
        )

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id == application_id,
            models.DBApplication.user_id == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    if application.status == update_data.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The application is already marked "
                f"as {update_data.status}."
            ),
        )

    previous_status = application.status
    application.status = update_data.status

    cleaned_reason = update_data.reason.strip()

    if not cleaned_reason:
        cleaned_reason = (
            "Status change confirmed from email analysis."
        )

    # NEW: Record why the email-based change occurred.
    history_event = models.DBApplicationStatusHistory(
        application_id=application.id,
        old_status=previous_status,
        new_status=update_data.status,
        source=update_data.analysis_source,
        reason=cleaned_reason,
        confidence=update_data.confidence,
    )

    db.add(history_event)
    db.commit()

    db.refresh(application)
    db.refresh(history_event)

    return {
        "message": "Email-based status update confirmed.",
        "application": application,
        "history_event": history_event,
    }

# --- HEALTH CHECK ---

@app.get("/")
def read_root():
    return {
        "status": "healthy",
        "database": "connected",
    }


# --- JOB APPLICATION ENDPOINTS ---

@app.post(
    "/api/v1/applications",
    response_model=schemas.ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    application: schemas.ApplicationCreate,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Creates an application for the authenticated user.
    """

    new_application = models.DBApplication(
        company_name=application.company_name.strip(),
        job_title=application.job_title.strip(),
        date_applied=date.today(),
        location=(
            application.location.strip()
            if application.location
            else None
        ),
        job_link=(
            application.job_link.strip()
            if application.job_link
            else None
        ),
        personal_notes=application.personal_notes,
        status="Applied",
        user_id=current_user.id,
    )

    db.add(new_application)

    # Gives the application an ID before the transaction commits.
    db.flush()

    # NEW: Record the application's initial status.
    initial_status_event = models.DBApplicationStatusHistory(
        application_id=new_application.id,
        old_status=None,
        new_status="Applied",
        source="initial",
        reason="Application was added to the tracker.",
    )

    db.add(initial_status_event)
    db.commit()
    db.refresh(new_application)

    return new_application


@app.get(
    "/api/v1/applications",
    response_model=list[schemas.ApplicationResponse],
)
def get_applications(
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns only applications owned by the authenticated user.
    """

    applications = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.user_id == current_user.id
        )
        .order_by(
            models.DBApplication.date_applied.desc(),
            models.DBApplication.id.desc(),
        )
        .all()
    )

    return applications


@app.patch(
    "/api/v1/applications/{application_id}",
    response_model=schemas.ApplicationResponse,
)
def update_application(
    application_id: int,
    application_update: schemas.ApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Updates supplied fields on an application owned by the
    authenticated user.

    Omitted fields remain unchanged. A status-history event is
    created only when this edit actually changes the status.
    """

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id == application_id,
            models.DBApplication.user_id == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    supplied_fields = application_update.model_dump(
        exclude_unset=True
    )

    if not supplied_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update.",
        )

    for required_field, display_name in (
        ("company_name", "Company name"),
        ("job_title", "Job title"),
    ):
        if required_field not in supplied_fields:
            continue

        value = supplied_fields[required_field]

        if value is None or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{display_name} cannot be blank.",
            )

        supplied_fields[required_field] = value.strip()

    if (
        "date_applied" in supplied_fields
        and supplied_fields["date_applied"] is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application date cannot be blank.",
        )

    if (
        "status" in supplied_fields
        and supplied_fields["status"] is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application status cannot be blank.",
        )

    for optional_field in (
        "location",
        "job_link",
        "personal_notes",
    ):
        if optional_field not in supplied_fields:
            continue

        value = supplied_fields[optional_field]

        if isinstance(value, str):
            supplied_fields[optional_field] = (
                value.strip() or None
            )

    previous_status = application.status

    for field_name, value in supplied_fields.items():
        setattr(application, field_name, value)

    if (
        "status" in supplied_fields
        and application.status != previous_status
    ):
        status_event = models.DBApplicationStatusHistory(
            application_id=application.id,
            old_status=previous_status,
            new_status=application.status,
            source="manual",
            reason="Application status changed while editing.",
        )

        db.add(status_event)

    db.commit()
    db.refresh(application)

    return application


@app.patch(
    "/api/v1/applications/{application_id}/status",
    response_model=schemas.ApplicationResponse,
)
def update_application_status(
    application_id: int,
    status_update: schemas.ApplicationStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Manually changes an application's status and records the change.
    """

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id == application_id,
            models.DBApplication.user_id == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    if application.status == status_update.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The application is already marked "
                f"as {status_update.status}."
            ),
        )

    previous_status = application.status
    application.status = status_update.status

    # NEW: Save a permanent record of the status change.
    status_event = models.DBApplicationStatusHistory(
        application_id=application.id,
        old_status=previous_status,
        new_status=status_update.status,
        source="manual",
        reason=(
            status_update.reason.strip()
            if status_update.reason
            else None
        ),
    )

    db.add(status_event)
    db.commit()
    db.refresh(application)

    return application


@app.get(
    "/api/v1/applications/{application_id}/status-history",
    response_model=list[
        schemas.ApplicationStatusHistoryResponse
    ],
)
def get_application_status_history(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Returns the status history for one owned application.
    """

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id == application_id,
            models.DBApplication.user_id == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    history = (
        db.query(models.DBApplicationStatusHistory)
        .filter(
            models.DBApplicationStatusHistory.application_id
            == application.id
        )
        .order_by(
            models.DBApplicationStatusHistory.created_at.asc(),
            models.DBApplicationStatusHistory.id.asc(),
        )
        .all()
    )

    return history


@app.delete("/api/v1/applications/{application_id}")
def delete_application(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.DBUser = Depends(get_current_user),
):
    """
    Deletes an application only when it belongs to the current user.
    """

    application = (
        db.query(models.DBApplication)
        .filter(
            models.DBApplication.id == application_id,
            models.DBApplication.user_id == current_user.id,
        )
        .first()
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    db.delete(application)
    db.commit()

    return {
        "message": "Application deleted successfully.",
        "application_id": application_id,
    }
