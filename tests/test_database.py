import unittest
import uuid
from pathlib import Path

from leadgen_ai.database import Database
from leadgen_ai.models import Lead, Message


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.db_path = scratch / f"database-{uuid.uuid4().hex}.db"
        self.database = Database(self.db_path)
        self.database.initialize()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.db_path}{suffix}")
            if path.exists():
                path.unlink()

    def test_upsert_merges_evidence_and_preserves_terminal_status(self):
        original = self.database.upsert_lead(
            Lead(
                domain="acme.test",
                company_name="Acme",
                website_url="https://acme.test/",
                source_url="https://acme.test/",
                emails=["careers@acme.test"],
                status="contacted",
            )
        )
        updated = self.database.upsert_lead(
            Lead(
                domain="acme.test",
                company_name="Acme Labs",
                website_url="https://acme.test/",
                source_url="https://acme.test/careers",
                hiring_signals=["we are hiring"],
                score=70,
                status="review",
            )
        )
        self.assertEqual(original.id, updated.id)
        self.assertEqual("contacted", updated.status)
        self.assertIn("careers@acme.test", updated.emails)
        self.assertIn("we are hiring", updated.hiring_signals)

    def test_messages_are_deduplicated_and_require_draft_for_approval(self):
        lead = self.database.upsert_lead(
            Lead(
                domain="acme.test",
                company_name="Acme",
                website_url="https://acme.test/",
                source_url="https://acme.test/",
            )
        )
        first = self.database.create_message(
            Message(lead_id=lead.id, channel="email", recipient="careers@acme.test", subject="A", body="One")
        )
        second = self.database.create_message(
            Message(lead_id=lead.id, channel="email", recipient="careers@acme.test", subject="B", body="Two")
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual("Two", second.body)
        self.database.approve_message(second.id)
        self.assertEqual("approved", self.database.list_messages()[0].status)
        with self.assertRaises(ValueError):
            self.database.approve_message(second.id)

    def test_domain_do_not_contact_blocks_email_on_domain(self):
        self.database.add_do_not_contact("acme.test", "opt out")
        self.assertTrue(self.database.is_do_not_contact("careers@acme.test"))
        self.assertFalse(self.database.is_do_not_contact("careers@other.test"))


if __name__ == "__main__":
    unittest.main()
