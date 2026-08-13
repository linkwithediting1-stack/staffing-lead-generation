import unittest

from leadgen_ai.models import Lead
from leadgen_ai.config import AppConfig
from leadgen_ai.policy import UrlPolicy, canonicalize_url
from leadgen_ai.scoring import score_lead


class PolicyAndScoringTests(unittest.TestCase):
    def test_url_canonicalization_removes_tracking(self):
        value = canonicalize_url("HTTPS://WWW.Example.com/jobs?utm_source=x&role=dev#top")
        self.assertEqual("https://www.example.com/jobs?role=dev", value)

    def test_policy_rejects_denied_and_private_ip_domains(self):
        policy = UrlPolicy(denied_domains=["instagram.com"])
        with self.assertRaises(ValueError):
            policy.validate("https://www.instagram.com/company")
        with self.assertRaises(ValueError):
            policy.validate("http://127.0.0.1/private")

    def test_rules_score_real_evidence(self):
        lead = Lead(
            domain="acme.test",
            company_name="Acme",
            website_url="https://acme.test/",
            source_url="https://acme.test/",
            location="Chandigarh",
            industry="SaaS",
            emails=["careers@acme.test"],
            job_urls=["https://acme.test/careers"],
            hiring_signals=["we are hiring", "open positions"],
            evidence_urls=["https://acme.test/", "https://acme.test/careers"],
            description="Cloud software company",
        )
        scored = score_lead(lead, ["Chandigarh"], ["SaaS"])
        self.assertGreaterEqual(scored.score, 75)
        self.assertIn("target-location", scored.score_reason)

    def test_remote_api_endpoints_require_https(self):
        config = AppConfig()
        config.ai.enabled = True
        config.ai.model = "small-model"
        config.ai.base_url = "http://api.example.test/v1"
        with self.assertRaises(ValueError):
            config.validate()
        config.ai.base_url = "http://127.0.0.1:11434/v1"
        config.validate()

    def test_plaintext_remote_smtp_is_rejected(self):
        config = AppConfig()
        config.smtp.host = "smtp.example.test"
        config.smtp.starttls = False
        config.smtp.ssl = False
        with self.assertRaises(ValueError):
            config.validate()


if __name__ == "__main__":
    unittest.main()
