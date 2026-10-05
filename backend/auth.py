# backend/auth.py
# NEW FILE: JWT creation and authentication dependencies.

import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

import models
from database import SessionLocal

# auth.py — NEW: Load variables from backend/.env
load_dotenv()
# Replace this development fallback before production.
# In production, JWT_SECRET_KEY must come from an environment variable
# or AWS Secrets Manager.
SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY",
    "development-only-secret-change-before-production",
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


# NEW: Swagger will ask for the JWT directly
bearer_scheme = HTTPBearer()


def get_db():
    """
    Creates a database session for authentication dependencies.
    """

    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def create_access_token(user_id: int) -> str:
    """
    Creates a signed JWT containing the authenticated user's ID.
    """

    expiration_time = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    token_payload = {
        "sub": str(user_id),
        "exp": expiration_time,
    }

    encoded_token = jwt.encode(
        token_payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return encoded_token


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.DBUser:
    token = credentials.credentials
    """
    Validates the JWT and returns the corresponding database user.
    """

    authentication_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        user_id_value = payload.get("sub")

        if user_id_value is None:
            raise authentication_error

        user_id = int(user_id_value)

    except (InvalidTokenError, ValueError, TypeError):
        raise authentication_error

    user = (
        db.query(models.DBUser)
        .filter(models.DBUser.id == user_id)
        .first()
    )

    if user is None:
        raise authentication_error

    return user