import csv
import unittest
import uuid
from pathlib import Path

from leadgen_ai.config import AppConfig
from leadgen_ai.database import Database
from leadgen_ai.importers import import_contact_history
from leadgen_ai.models import Lead
from leadgen_ai.outreach import create_outreach_drafts


class ImportAndOutreachTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.root = scratch
        self.token = uuid.uuid4().hex
        self.db_path = self.root / f"import-{self.token}.db"
        self.database = Database(self.db_path)
        self.database.initialize()

    def tearDown(self):
        for path in self.root.glob(f"*{self.token}*"):
            if path.is_file():
                path.unlink()

    def test_imported_sent_history_prevents_repeat_contact(self):
        source = self.root / f"history-{self.token}.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Company", "Website", "Email", "Status"])
            writer.writeheader()
            writer.writerow(
                {
                    "Company": "Acme",
                    "Website": "https://acme.test",
                    "Email": "careers@acme.test",
                    "Status": "Message Sent",
                }
            )
            writer.writerow(
                {
                    "Company": "Fresh",
                    "Website": "fresh.test",
                    "Email": "hiring@fresh.test",
                    "Status": "Not Sent",
                }
            )
        report = import_contact_history(self.database, source)
        self.assertEqual(2, report["leads"])
        self.assertTrue(self.database.is_do_not_contact("careers@acme.test"))
        self.assertFalse(self.database.is_do_not_contact("hiring@fresh.test"))
        self.assertEqual("contacted", self.database.get_lead_by_domain("acme.test").status)
        self.assertEqual("new", self.database.get_lead_by_domain("fresh.test").status)

    def test_draft_creation_skips_do_not_contact_and_deduplicates(self):
        config = AppConfig(config_directory=self.root)
        config.outreach.minimum_score = 60
        lead = self.database.upsert_lead(
            Lead(
                domain="fresh.test",
                company_name="Fresh",
                website_url="https://fresh.test/",
                source_url="https://fresh.test/careers",
                emails=["hiring@fresh.test"],
                hiring_signals=["open positions"],
                score=80,
                status="approved",
            )
        )
        first = create_outreach_drafts(config, self.database)
        second = create_outreach_drafts(config, self.database)
        self.assertEqual(1, len(first))
        self.assertEqual(first[0].id, second[0].id)
        self.database.add_do_not_contact(lead.domain, "manual")
        self.assertEqual([], create_outreach_drafts(config, self.database))

    def test_draft_creation_skips_person_named_mailbox(self):
        config = AppConfig(config_directory=self.root)
        config.outreach.minimum_score = 60
        self.database.upsert_lead(
            Lead(
                domain="personal.test",
                company_name="Personal",
                website_url="https://personal.test/",
                source_url="https://personal.test/careers",
                emails=["jane@personal.test"],
                score=90,
                status="approved",
            )
        )
        self.assertEqual([], create_outreach_drafts(config, self.database))


if __name__ == "__main__":
    unittest.main()
