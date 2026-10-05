# backend/models.py
# UPDATED: Added application status history.

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )
    hashed_password = Column(String, nullable=False)

    applications = relationship(
        "DBApplication",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    # NEW: One Gmail connection per user.
    gmail_connection = relationship(
        "DBGmailConnection",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DBApplication(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    job_title = Column(String, nullable=False)
    date_applied = Column(Date, nullable=False)
    location = Column(String, nullable=True)
    job_link = Column(String, nullable=True)
    personal_notes = Column(String, nullable=True)
    status = Column(
        String,
        nullable=False,
        default="Applied",
    )

    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    owner = relationship(
        "DBUser",
        back_populates="applications",
    )

    # NEW: Complete status-change history.
    status_history = relationship(
        "DBApplicationStatusHistory",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="DBApplicationStatusHistory.created_at",
    )


class DBApplicationStatusHistory(Base):
    """
    Stores every status change made to an application.
    """

    __tablename__ = "application_status_history"

    id = Column(Integer, primary_key=True, index=True)

    application_id = Column(
        Integer,
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    old_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)

    # Examples: initial, manual, rule, ai, gmail
    source = Column(String, nullable=False)

    reason = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)

    # Used later to prevent processing one Gmail message twice.
    email_message_id = Column(
        String,
        nullable=True,
        unique=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    application = relationship(
        "DBApplication",
        back_populates="status_history",
    )

class DBGmailConnection(Base):
    """
    Stores one encrypted Gmail OAuth connection per user.
    """

    __tablename__ = "gmail_connections"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    gmail_email = Column(
        String,
        nullable=False,
    )

    encrypted_refresh_token = Column(
        Text,
        nullable=False,
    )

    scopes = Column(
        Text,
        nullable=False,
        default=(
            "https://www.googleapis.com/auth/"
            "gmail.readonly"
        ),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

        # NEW: Whether the user enabled automatic Gmail monitoring.
    automatic_monitoring_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    # NEW: Gmail mailbox history position used for partial syncs.
    gmail_history_id = Column(
        String,
        nullable=True,
    )

    # NEW: When the current Gmail push watch expires.
    watch_expiration = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    user = relationship(
        "DBUser",
        back_populates="gmail_connection",
    )

    processing_jobs = relationship(
        "DBGmailProcessingJob",
        back_populates="gmail_connection",
        cascade="all, delete-orphan",
    )


class DBGmailProcessingJob(Base):
    """
    Stores one durable unit of work created by a Gmail Pub/Sub push.
    """

    __tablename__ = "gmail_processing_jobs"

    __table_args__ = (
        UniqueConstraint(
            "gmail_connection_id",
            "history_id",
            name="uq_gmail_processing_job_connection_history",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    gmail_connection_id = Column(
        Integer,
        ForeignKey(
            "gmail_connections.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    history_id = Column(String, nullable=False)
    pubsub_message_id = Column(String, nullable=True)

    # The future worker will move jobs through pending/processing/completed/failed.
    status = Column(
        String,
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    processed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    gmail_connection = relationship(
        "DBGmailConnection",
        back_populates="processing_jobs",
    )


class DBGmailSuggestion(Base):
    """
    Stores one Gmail analysis suggestion without automatically
    changing the matched application.
    """

    __tablename__ = "gmail_suggestions"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "gmail_message_id",
            name="uq_gmail_suggestion_user_message",
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    application_id = Column(
        Integer,
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    gmail_message_id = Column(
        String,
        nullable=False,
        index=True,
    )

    gmail_thread_id = Column(
        String,
        nullable=True,
    )

    sender = Column(
        Text,
        nullable=True,
    )

    subject = Column(
        Text,
        nullable=True,
    )

    received_at = Column(
        Text,
        nullable=True,
    )

    is_job_related = Column(
        Boolean,
        nullable=False,
    )

    suggested_status = Column(
        String,
        nullable=False,
    )

    confidence = Column(
        Float,
        nullable=False,
    )

    reason = Column(
        Text,
        nullable=False,
    )

    analysis_source = Column(
        String,
        nullable=False,
    )

    match_score = Column(
        Integer,
        nullable=True,
    )

    requires_confirmation = Column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Values used later: pending, confirmed, ignored.
    review_status = Column(
        String,
        nullable=False,
        default="pending",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )