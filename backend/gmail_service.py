# backend/gmail_service.py
# NEW: Authenticates with Gmail and retrieves limited message previews.

import base64
import binascii
import os
import re
from html import unescape
from html.parser import HTMLParser

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import models
from gmail_oauth import GMAIL_SCOPES
from token_encryption import decrypt_refresh_token


load_dotenv()


MAX_EMAIL_BODY_CHARS = 20_000


class EmailHTMLTextExtractor(HTMLParser):
    """Converts basic email HTML into readable text without dependencies."""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored_depth += 1
        elif tag in {"br", "div", "li", "p", "tr"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in {"div", "li", "p", "tr"}:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.text_parts.append(data)

    def get_text(self) -> str:
        return "".join(self.text_parts)


def clean_email_text(value: str) -> str:
    """Normalizes whitespace while preserving useful paragraph breaks."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()[:MAX_EMAIL_BODY_CHARS]


def html_to_text(value: str) -> str:
    """Extracts visible text from an HTML-only email body."""

    parser = EmailHTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return clean_email_text(unescape(parser.get_text()))


def decode_gmail_body_data(encoded_data: str) -> str:
    """Decodes Gmail's URL-safe base64 message-part data."""

    if not encoded_data:
        return ""

    padded_data = encoded_data + "=" * (-len(encoded_data) % 4)

    try:
        decoded_bytes = base64.urlsafe_b64decode(
            padded_data.encode("ascii")
        )
    except (ValueError, UnicodeEncodeError, binascii.Error):
        return ""

    return decoded_bytes.decode("utf-8", errors="replace")


def extract_gmail_message_text(payload: dict) -> str:
    """Extracts plain text, or an HTML fallback, from a Gmail payload."""

    plain_text_parts = []
    html_parts = []

    def visit_part(part: dict) -> None:
        for child_part in part.get("parts", []):
            visit_part(child_part)

        body = part.get("body", {})
        encoded_data = body.get("data", "")

        # Attachments must never be included in classifier input.
        if not encoded_data or part.get("filename"):
            return

        decoded_text = decode_gmail_body_data(encoded_data)
        mime_type = part.get("mimeType", "").lower()

        if mime_type == "text/plain":
            plain_text_parts.append(decoded_text)
        elif mime_type == "text/html":
            html_parts.append(decoded_text)

    visit_part(payload)

    if plain_text_parts:
        return clean_email_text("\n\n".join(plain_text_parts))

    if html_parts:
        return html_to_text("\n\n".join(html_parts))

    return ""


def create_gmail_service(
    gmail_connection: models.DBGmailConnection,
):
    """
    Builds an authenticated Gmail API service for one user.
    """

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Google OAuth credentials are not configured."
        )

    try:
        refresh_token = decrypt_refresh_token(
            gmail_connection.encrypted_refresh_token
        )
    except RuntimeError as error:
        raise RuntimeError(
            "The saved Gmail connection could not be decrypted."
        ) from error

    stored_scopes = (
        gmail_connection.scopes.split()
        if gmail_connection.scopes
        else GMAIL_SCOPES
    )

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=stored_scopes,
    )

    try:
        # NEW: Exchange the refresh token for a temporary access token.
        credentials.refresh(GoogleRequest())
    except RefreshError as error:
        raise RuntimeError(
            "The Gmail connection has expired or was revoked."
        ) from error

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


def get_header_value(
    headers: list[dict],
    header_name: str,
) -> str:
    """
    Finds one header value without assuming header order.
    """

    for header in headers:
        if header.get("name", "").lower() == header_name.lower():
            return header.get("value", "")

    return ""


def get_recent_gmail_previews(
    gmail_connection: models.DBGmailConnection,
    max_results: int,
) -> list[dict]:
    """
    Retrieves limited metadata and snippets from recent inbox messages.
    """

    gmail_service = create_gmail_service(
        gmail_connection=gmail_connection
    )

    try:
        list_response = (
            gmail_service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX"],
                q="newer_than:30d",
                maxResults=max_results,
            )
            .execute()
        )

        message_references = list_response.get(
            "messages",
            [],
        )

        previews = []

        for message_reference in message_references:
            message = (
                gmail_service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_reference["id"],
                    format="metadata",
                    metadataHeaders=[
                        "From",
                        "Subject",
                        "Date",
                    ],
                )
                .execute()
            )

            payload = message.get("payload", {})
            headers = payload.get("headers", [])

            previews.append(
                {
                    "gmail_message_id": message["id"],
                    "gmail_thread_id": message.get(
                        "threadId",
                        "",
                    ),
                    "sender": get_header_value(
                        headers,
                        "From",
                    ),
                    "subject": get_header_value(
                        headers,
                        "Subject",
                    ),
                    "received_at": get_header_value(
                        headers,
                        "Date",
                    ),
                    # Gmail supplies a short preview; no body is stored.
                    "snippet": message.get(
                        "snippet",
                        "",
                    )[:500],
                }
            )

        return previews

    except HttpError as error:
        raise RuntimeError(
            "Gmail could not retrieve recent messages."
        ) from error


def get_gmail_message_for_processing(
    gmail_service,
    gmail_message_id: str,
) -> dict:
    """
    Retrieves one complete email for transient internal processing.

    The returned body_text is passed to classification and matching but is
    never stored in the TrackToHire database or returned by preview APIs.
    """

    message = (
        gmail_service.users()
        .messages()
        .get(
            userId="me",
            id=gmail_message_id,
            format="full",
        )
        .execute()
    )

    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    snippet = message.get("snippet", "")[:500]
    body_text = extract_gmail_message_text(payload)

    # A snippet remains a safe fallback for malformed or empty MIME payloads.
    if not body_text:
        body_text = snippet

    return {
        "gmail_message_id": message["id"],
        "gmail_thread_id": message.get("threadId", ""),
        "sender": get_header_value(headers, "From"),
        "subject": get_header_value(headers, "Subject"),
        "received_at": get_header_value(headers, "Date"),
        "snippet": snippet,
        "body_text": body_text,
    }


def get_recent_gmail_messages_for_processing(
    gmail_connection: models.DBGmailConnection,
    max_results: int,
) -> list[dict]:
    """Retrieves recent full emails for manual synchronization."""

    gmail_service = create_gmail_service(
        gmail_connection=gmail_connection
    )

    try:
        list_response = (
            gmail_service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX"],
                q="newer_than:30d",
                maxResults=max_results,
            )
            .execute()
        )

        return [
            get_gmail_message_for_processing(
                gmail_service=gmail_service,
                gmail_message_id=message_reference["id"],
            )
            for message_reference in list_response.get("messages", [])
        ]
    except HttpError as error:
        raise RuntimeError(
            "Gmail could not retrieve recent messages for processing."
        ) from error


def start_gmail_watch(
    gmail_connection: models.DBGmailConnection,
) -> dict:
    """
    Starts or renews Gmail push notifications for one connected user.
    """

    topic_name = os.getenv("GMAIL_PUBSUB_TOPIC")

    if not topic_name:
        raise RuntimeError(
            "GMAIL_PUBSUB_TOPIC is not configured."
        )

    if not topic_name.startswith("projects/"):
        raise RuntimeError(
            "GMAIL_PUBSUB_TOPIC must be the complete Pub/Sub "
            "topic name."
        )

    gmail_service = create_gmail_service(
        gmail_connection=gmail_connection
    )

    watch_request = {
        "topicName": topic_name,

        # Only monitor changes involving inbox messages.
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }

    try:
        watch_response = (
            gmail_service.users()
            .watch(
                userId="me",
                body=watch_request,
            )
            .execute()
        )
    except HttpError as error:
        error_detail = getattr(
            error,
            "reason",
            "Gmail could not start automatic monitoring.",
        )

        raise RuntimeError(
            f"Gmail watch setup failed: {error_detail}"
        ) from error

    history_id = watch_response.get("historyId")
    expiration = watch_response.get("expiration")

    if not history_id or not expiration:
        raise RuntimeError(
            "Gmail did not return complete watch information."
        )

    return {
        "history_id": str(history_id),
        "expiration": str(expiration),
    }

def get_gmail_message_preview(
    gmail_service,
    gmail_message_id: str,
) -> dict:
    """
    Retrieves limited metadata for one Gmail message.
    """

    message = (
        gmail_service.users()
        .messages()
        .get(
            userId="me",
            id=gmail_message_id,
            format="metadata",
            metadataHeaders=[
                "From",
                "Subject",
                "Date",
            ],
        )
        .execute()
    )

    payload = message.get("payload", {})
    headers = payload.get("headers", [])

    return {
        "gmail_message_id": message["id"],
        "gmail_thread_id": message.get(
            "threadId",
            "",
        ),
        "sender": get_header_value(
            headers,
            "From",
        ),
        "subject": get_header_value(
            headers,
            "Subject",
        ),
        "received_at": get_header_value(
            headers,
            "Date",
        ),
        # Gmail supplies a short preview; no full body is stored.
        "snippet": message.get(
            "snippet",
            "",
        )[:500],
    }


def get_gmail_history_previews(
    gmail_connection: models.DBGmailConnection,
    start_history_id: str,
) -> dict:
    """
    Retrieves newly added inbox messages after one saved Gmail
    history position.

    Returns unique processing messages and Gmail's latest history ID.
    """

    if not start_history_id:
        raise RuntimeError(
            "The Gmail connection does not have a saved history ID."
        )

    gmail_service = create_gmail_service(
        gmail_connection=gmail_connection
    )

    page_token = None
    latest_history_id = start_history_id
    message_ids = set()

    try:
        while True:
            request_arguments = {
                "userId": "me",
                "startHistoryId": start_history_id,
                "historyTypes": ["messageAdded"],
                "labelId": "INBOX",
                "maxResults": 100,
            }

            if page_token:
                request_arguments["pageToken"] = page_token

            history_response = (
                gmail_service.users()
                .history()
                .list(**request_arguments)
                .execute()
            )

            latest_history_id = str(
                history_response.get(
                    "historyId",
                    latest_history_id,
                )
            )

            for history_record in history_response.get(
                "history",
                [],
            ):
                for added_message in history_record.get(
                    "messagesAdded",
                    [],
                ):
                    message = added_message.get(
                        "message",
                        {},
                    )

                    message_id = message.get("id")
                    label_ids = message.get(
                        "labelIds",
                        [],
                    )

                    if (
                        message_id
                        and "INBOX" in label_ids
                    ):
                        message_ids.add(message_id)

            page_token = history_response.get(
                "nextPageToken"
            )

            if not page_token:
                break

        processing_messages = []

        for message_id in message_ids:
            processing_messages.append(
                get_gmail_message_for_processing(
                    gmail_service=gmail_service,
                    gmail_message_id=message_id,
                )
            )

        return {
            "latest_history_id": latest_history_id,
            "messages": processing_messages,
        }

    except HttpError as error:
        status_code = getattr(
            error.resp,
            "status",
            None,
        )

        if status_code == 404:
            raise RuntimeError(
                "The saved Gmail history ID is too old or invalid. "
                "Run a manual Gmail sync and restart monitoring."
            ) from error

        raise RuntimeError(
            "Gmail could not retrieve mailbox history."
        ) from error
