# backend/token_encryption.py
# NEW: Encrypts and decrypts sensitive OAuth refresh tokens.

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv


load_dotenv()


def get_token_cipher() -> Fernet:
    """
    Creates the Fernet cipher using the backend environment key.
    """

    encryption_key = os.getenv(
        "GMAIL_TOKEN_ENCRYPTION_KEY"
    )

    if not encryption_key:
        raise RuntimeError(
            "GMAIL_TOKEN_ENCRYPTION_KEY is not configured."
        )

    try:
        return Fernet(
            encryption_key.encode("utf-8")
        )
    except (ValueError, TypeError) as error:
        raise RuntimeError(
            "GMAIL_TOKEN_ENCRYPTION_KEY is invalid."
        ) from error


def encrypt_refresh_token(refresh_token: str) -> str:
    """
    Encrypts a Google OAuth refresh token before database storage.
    """

    cipher = get_token_cipher()

    encrypted_token = cipher.encrypt(
        refresh_token.encode("utf-8")
    )

    return encrypted_token.decode("utf-8")


def decrypt_refresh_token(
    encrypted_refresh_token: str,
) -> str:
    """
    Decrypts a stored Google OAuth refresh token.
    """

    cipher = get_token_cipher()

    try:
        decrypted_token = cipher.decrypt(
            encrypted_refresh_token.encode("utf-8")
        )
    except InvalidToken as error:
        raise RuntimeError(
            "The stored Gmail token could not be decrypted."
        ) from error

    return decrypted_token.decode("utf-8")