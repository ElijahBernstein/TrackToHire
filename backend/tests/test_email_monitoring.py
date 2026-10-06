import base64
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError


# main.py creates missing tables during import. Use an isolated test database.
TEST_DATABASE_DIRECTORY = tempfile.TemporaryDirectory()
TEST_DATABASE_PATH = Path(TEST_DATABASE_DIRECTORY.name) / "email-tests.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

import main
import models
import schemas
from ai_service import validate_ai_classification
from gmail_service import extract_gmail_message_text
from database import Base, SessionLocal, engine


class EmailMonitoringRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        TEST_DATABASE_DIRECTORY.cleanup()

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def test_short_company_name_does_not_match_inside_other_words(self):
        application = SimpleNamespace(
            id=19,
            company_name="AI",
            job_title="Associate Engineer Intern",
            status="Applied",
        )

        result = main.score_application_match(
            application=application,
            sender="Udemy <hello@students.udemy.com>",
            subject="Elijah, not sure what course is right for you?",
            body="Find a course that matches your goals.",
        )

        self.assertEqual(result["match_score"], 0)

    def test_disabling_monitoring_keeps_gmail_connected(self):
        gmail_connection = SimpleNamespace(
            automatic_monitoring_enabled=True,
            watch_expiration="future",
        )

        class FakeQuery:
            def filter(self, *args):
                return self

            def first(self):
                return gmail_connection

        class FakeDatabase:
            def __init__(self):
                self.committed = False

            def query(self, *args):
                return FakeQuery()

            def commit(self):
                self.committed = True

            def refresh(self, *args):
                return None

        fake_db = FakeDatabase()

        result = main.stop_gmail_monitoring(
            db=fake_db,
            current_user=SimpleNamespace(id=7),
        )

        self.assertFalse(
            gmail_connection.automatic_monitoring_enabled
        )
        self.assertIsNone(gmail_connection.watch_expiration)
        self.assertTrue(fake_db.committed)
        self.assertFalse(result["monitoring_active"])

    def test_user_can_select_owned_application_when_confirming(self):
        db = SessionLocal()

        try:
            user = models.DBUser(
                email="owner@example.com",
                hashed_password="test-hash",
            )
            db.add(user)
            db.flush()

            application = models.DBApplication(
                company_name="Roblox",
                job_title="Software Engineer",
                date_applied=date.today(),
                status="Applied",
                user_id=user.id,
            )
            db.add(application)
            db.flush()

            suggestion = models.DBGmailSuggestion(
                user_id=user.id,
                application_id=None,
                gmail_message_id="manual-match-message",
                is_job_related=True,
                suggested_status="Assessment",
                confidence=0.90,
                reason="The employer requested an assessment.",
                analysis_source="ai",
                requires_confirmation=True,
                review_status="pending",
            )
            db.add(suggestion)
            db.commit()

            result = main.confirm_gmail_suggestion(
                suggestion_id=suggestion.id,
                confirmation=schemas.GmailSuggestionConfirmRequest(
                    application_id=application.id,
                ),
                db=db,
                current_user=user,
            )

            self.assertEqual(application.status, "Assessment")
            self.assertEqual(suggestion.application_id, application.id)
            self.assertEqual(suggestion.review_status, "confirmed")
            self.assertEqual(
                result["history_event"].email_message_id,
                "manual-match-message",
            )
        finally:
            db.close()

    def test_user_cannot_select_another_users_application(self):
        db = SessionLocal()

        try:
            owner = models.DBUser(
                email="owner@example.com",
                hashed_password="test-hash",
            )
            other_user = models.DBUser(
                email="other@example.com",
                hashed_password="test-hash",
            )
            db.add_all([owner, other_user])
            db.flush()

            other_application = models.DBApplication(
                company_name="Private Company",
                job_title="Private Role",
                date_applied=date.today(),
                status="Applied",
                user_id=other_user.id,
            )
            db.add(other_application)
            db.flush()

            suggestion = models.DBGmailSuggestion(
                user_id=owner.id,
                application_id=None,
                gmail_message_id="cross-user-attempt",
                is_job_related=True,
                suggested_status="Interview",
                confidence=0.90,
                reason="Interview requested.",
                analysis_source="ai",
                requires_confirmation=True,
                review_status="pending",
            )
            db.add(suggestion)
            db.commit()

            with self.assertRaises(main.HTTPException) as context:
                main.confirm_gmail_suggestion(
                    suggestion_id=suggestion.id,
                    confirmation=schemas.GmailSuggestionConfirmRequest(
                        application_id=other_application.id,
                    ),
                    db=db,
                    current_user=owner,
                )

            self.assertEqual(context.exception.status_code, 404)
            self.assertEqual(other_application.status, "Applied")
            self.assertIsNone(suggestion.application_id)
        finally:
            db.close()

    def test_owner_can_edit_all_application_fields(self):
        db = SessionLocal()

        try:
            user = models.DBUser(
                email="editor@example.com",
                hashed_password="test-hash",
            )
            db.add(user)
            db.flush()

            application = models.DBApplication(
                company_name="Old Company",
                job_title="Old Role",
                date_applied=date(2026, 9, 1),
                location="Old Location",
                job_link="https://old.example.com",
                personal_notes="Old notes",
                status="Applied",
                user_id=user.id,
            )
            db.add(application)
            db.commit()

            result = main.update_application(
                application_id=application.id,
                application_update=schemas.ApplicationUpdate(
                    company_name="New Company",
                    job_title="Cloud Engineering Intern",
                    date_applied=date(2026, 10, 5),
                    location="Remote",
                    job_link="https://new.example.com/job",
                    personal_notes="Updated notes",
                    status="Interview",
                ),
                db=db,
                current_user=user,
            )

            self.assertEqual(result.company_name, "New Company")
            self.assertEqual(
                result.job_title,
                "Cloud Engineering Intern",
            )
            self.assertEqual(result.date_applied, date(2026, 10, 5))
            self.assertEqual(result.location, "Remote")
            self.assertEqual(
                result.job_link,
                "https://new.example.com/job",
            )
            self.assertEqual(result.personal_notes, "Updated notes")
            self.assertEqual(result.status, "Interview")

            history = (
                db.query(models.DBApplicationStatusHistory)
                .filter(
                    models.DBApplicationStatusHistory.application_id
                    == application.id
                )
                .one()
            )

            self.assertEqual(history.old_status, "Applied")
            self.assertEqual(history.new_status, "Interview")
            self.assertEqual(history.source, "manual")
        finally:
            db.close()

    def test_partial_edit_preserves_unchanged_fields(self):
        db = SessionLocal()

        try:
            user = models.DBUser(
                email="partial-editor@example.com",
                hashed_password="test-hash",
            )
            db.add(user)
            db.flush()

            application = models.DBApplication(
                company_name="Example Company",
                job_title="Software Engineer Intern",
                date_applied=date(2026, 9, 15),
                location="Seattle, WA",
                job_link="https://example.com/job",
                personal_notes="Keep these notes",
                status="Assessment",
                user_id=user.id,
            )
            db.add(application)
            db.commit()

            result = main.update_application(
                application_id=application.id,
                application_update=schemas.ApplicationUpdate(
                    location="Bellingham, WA",
                ),
                db=db,
                current_user=user,
            )

            self.assertEqual(result.location, "Bellingham, WA")
            self.assertEqual(result.company_name, "Example Company")
            self.assertEqual(
                result.job_title,
                "Software Engineer Intern",
            )
            self.assertEqual(result.status, "Assessment")
            self.assertEqual(result.personal_notes, "Keep these notes")

            history_count = (
                db.query(models.DBApplicationStatusHistory)
                .filter(
                    models.DBApplicationStatusHistory.application_id
                    == application.id
                )
                .count()
            )

            self.assertEqual(history_count, 0)
        finally:
            db.close()

    def test_user_cannot_edit_another_users_application(self):
        db = SessionLocal()

        try:
            owner = models.DBUser(
                email="application-owner@example.com",
                hashed_password="test-hash",
            )
            other_user = models.DBUser(
                email="unauthorized-editor@example.com",
                hashed_password="test-hash",
            )
            db.add_all([owner, other_user])
            db.flush()

            application = models.DBApplication(
                company_name="Private Company",
                job_title="Private Role",
                date_applied=date.today(),
                status="Applied",
                user_id=owner.id,
            )
            db.add(application)
            db.commit()

            with self.assertRaises(main.HTTPException) as context:
                main.update_application(
                    application_id=application.id,
                    application_update=schemas.ApplicationUpdate(
                        company_name="Unauthorized Change",
                    ),
                    db=db,
                    current_user=other_user,
                )

            self.assertEqual(context.exception.status_code, 404)
            self.assertEqual(application.company_name, "Private Company")
        finally:
            db.close()

    def test_application_edit_rejects_unknown_status(self):
        with self.assertRaises(ValidationError):
            schemas.ApplicationUpdate(status="Hired")

    def test_nested_mime_payload_prefers_complete_plain_text(self):
        plain_text = (
            "Thank you for completing the assessment.\n"
            "We are pursuing other candidates."
        )
        encoded_text = base64.urlsafe_b64encode(
            plain_text.encode("utf-8")
        ).decode("ascii")

        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": encoded_text},
                },
                {
                    "mimeType": "text/html",
                    "filename": "",
                    "body": {"data": ""},
                },
            ],
        }

        self.assertEqual(
            extract_gmail_message_text(payload),
            plain_text,
        )

    def test_html_only_payload_extracts_visible_text(self):
        html_body = (
            "<html><style>.hidden { display:none; }</style>"
            "<body><p>Interview invitation</p>"
            "<script>ignoreThis()</script><p>Choose a time.</p></body></html>"
        )
        encoded_html = base64.urlsafe_b64encode(
            html_body.encode("utf-8")
        ).decode("ascii")

        result = extract_gmail_message_text(
            {
                "mimeType": "text/html",
                "filename": "",
                "body": {"data": encoded_html},
            }
        )

        self.assertIn("Interview invitation", result)
        self.assertIn("Choose a time.", result)
        self.assertNotIn("ignoreThis", result)
        self.assertNotIn("display:none", result)

    def test_verified_current_evidence_keeps_rejected_status(self):
        email_body = (
            "Thank you for completing our online assessments. "
            "We are pursuing candidates whose abilities more closely "
            "match the needs of this position."
        )
        classification = schemas.AIEmailClassification(
            is_job_related=True,
            suggested_status="Rejected",
            confidence=0.97,
            reason="The employer is pursuing other candidates.",
            evidence=(
                "We are pursuing candidates whose abilities more closely "
                "match the needs of this position."
            ),
            event_is_current=True,
        )

        result = validate_ai_classification(
            result=classification,
            sender="CTC Campus Recruiting Team",
            subject="Decision on application",
            body=email_body,
        )

        self.assertEqual(result.suggested_status, "Rejected")
        self.assertIn("Evidence:", result.reason)

    def test_invented_evidence_is_downgraded_to_unknown(self):
        classification = schemas.AIEmailClassification(
            is_job_related=True,
            suggested_status="Offer",
            confidence=0.99,
            reason="The employer made an offer.",
            evidence="We are delighted to offer you the position.",
            event_is_current=True,
        )

        result = validate_ai_classification(
            result=classification,
            sender="Recruiting Team",
            subject="Application update",
            body="We are still reviewing your application.",
        )

        self.assertEqual(result.suggested_status, "Unknown")
        self.assertLessEqual(result.confidence, 0.40)
        self.assertEqual(result.evidence, "")

    def test_historical_stage_is_downgraded_to_unknown(self):
        email_body = "Thank you for completing the online assessment."
        classification = schemas.AIEmailClassification(
            is_job_related=True,
            suggested_status="Assessment",
            confidence=0.95,
            reason="The email mentions an assessment.",
            evidence="completing the online assessment",
            event_is_current=False,
        )

        result = validate_ai_classification(
            result=classification,
            sender="Recruiting Team",
            subject="Application update",
            body=email_body,
        )

        self.assertEqual(result.suggested_status, "Unknown")
        self.assertLessEqual(result.confidence, 0.40)

    def test_unrelated_email_cannot_return_an_application_status(self):
        classification = schemas.AIEmailClassification(
            is_job_related=False,
            suggested_status="Offer",
            confidence=0.98,
            reason="Promotional sale.",
            evidence="special offer on car-care products",
            event_is_current=True,
        )

        result = validate_ai_classification(
            result=classification,
            sender="AutoZone",
            subject="Summer sale",
            body="Special offer on car-care products.",
        )

        self.assertEqual(result.suggested_status, "Unknown")
        self.assertFalse(result.event_is_current)
        self.assertEqual(result.evidence, "")

    def test_lone_45_point_match_is_not_attached(self):
        application = SimpleNamespace(
            id=19,
            company_name="Chicago Trading Company",
            job_title="Associate Engineer Intern",
            status="Applied",
        )

        match, requires_confirmation = main.find_best_application_match(
            user_applications=[application],
            sender="recruiting@example.com",
            subject="Update from Chicago Trading Company",
            body="We have an update for you.",
        )

        self.assertIsNone(match)
        self.assertTrue(requires_confirmation)

    @patch("main.classify_email_with_ai")
    def test_ctc_rejection_reaches_ai_instead_of_stopping_at_assessment(
        self,
        mock_classify,
    ):
        mock_classify.return_value = SimpleNamespace(
            is_job_related=True,
            suggested_status="Rejected",
            confidence=0.97,
            reason="The employer is pursuing other candidates.",
        )

        result = main.get_hybrid_email_analysis(
            sender="CTC Campus Recruiting Team",
            subject="Decision on application",
            body=(
                "Thank you for completing our online assessments. "
                "We are pursuing candidates whose experience more closely "
                "matches the needs of this position."
            ),
        )

        self.assertTrue(result["ai_was_used"])
        self.assertEqual(result["suggested_status"], "Rejected")

    @patch("main.get_hybrid_email_analysis")
    def test_ai_declared_marketing_email_is_ignored(
        self,
        mock_analysis,
    ):
        mock_analysis.return_value = {
            "is_job_related": False,
            "suggested_status": "Unknown",
            "confidence": 0.98,
            "reason": "Promotional car-care offer.",
            "analysis_source": "ai",
            "ai_was_used": True,
        }

        db = SessionLocal()

        try:
            user = models.DBUser(
                email="email-test@example.com",
                hashed_password="not-used-in-this-test",
            )
            db.add(user)
            db.flush()

            application = models.DBApplication(
                company_name="AI",
                job_title="Associate Engineer Intern",
                date_applied=date.today(),
                status="Applied",
                user_id=user.id,
            )
            db.add(application)
            db.commit()

            result = main.process_gmail_messages(
                db=db,
                user_id=user.id,
                gmail_messages=[
                    {
                        "gmail_message_id": "autozone-test-message",
                        "gmail_thread_id": "autozone-test-thread",
                        "sender": "AutoZone <autozone@em.autozone.com>",
                        "subject": "Your Car Deserves a Summer Glow-Up",
                        "received_at": "test-date",
                        "snippet": "Short Gmail preview.",
                        "body_text": (
                            "The complete promotional car-care offer."
                        ),
                    }
                ],
            )

            db.commit()

            record = db.query(models.DBGmailSuggestion).one()

            self.assertEqual(result["suggestions_created"], 0)
            self.assertEqual(result["irrelevant_messages"], 1)
            self.assertEqual(record.review_status, "ignored")
            self.assertIsNone(record.application_id)
            self.assertIsNone(record.match_score)
            self.assertEqual(
                mock_analysis.call_args.kwargs["body"],
                "The complete promotional car-care offer.",
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
