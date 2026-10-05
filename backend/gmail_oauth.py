# backend/gmail_oauth.py
# NEW: Google OAuth configuration and secure state handling.

import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from jwt.exceptions import InvalidTokenError

from auth import ALGORITHM, SECRET_KEY


load_dotenv()


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_google_redirect_uri() -> str:
    """
    Returns the configured Google OAuth callback URI.
    """

    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

    if not redirect_uri:
        raise RuntimeError(
            "GOOGLE_REDIRECT_URI is not configured."
        )

    return redirect_uri


def create_google_oauth_flow(
    state: str | None = None,
) -> Flow:
    """
    Creates a Google OAuth web-server flow from environment variables.
    """

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = get_google_redirect_uri()

    if not client_id or not client_secret:
        raise RuntimeError(
            "Google OAuth credentials are not configured."
        )

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": (
                "https://accounts.google.com/o/oauth2/auth"
            ),
            "token_uri": (
                "https://oauth2.googleapis.com/token"
            ),
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=GMAIL_SCOPES,
        state=state,

        # This backend flow does not persist a PKCE verifier
        # between the connect endpoint and Google's callback.
        autogenerate_code_verifier=False,
    )

    flow.redirect_uri = redirect_uri

    return flow


def create_gmail_oauth_state(user_id: int) -> str:
    """
    Creates a short-lived signed state token tied to one app user.
    """

    payload = {
        "sub": str(user_id),
        "purpose": "gmail_oauth",
        "nonce": secrets.token_urlsafe(24),
        "exp": (
            datetime.now(timezone.utc)
            + timedelta(minutes=10)
        ),
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_gmail_oauth_state(state: str) -> int:
    """
    Validates OAuth state and returns the Cloud Job Tracker user ID.
    """

    try:
        payload = jwt.decode(
            state,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        if payload.get("purpose") != "gmail_oauth":
            raise RuntimeError(
                "Invalid Gmail OAuth state."
            )

        user_id = int(payload["sub"])

    except (
        InvalidTokenError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            "The Gmail authorization request is invalid or expired."
        ) from error

    return user_id