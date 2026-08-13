import unittest

from leadgen_ai.extractors import extract_page, pages_to_lead


HTML = """
<!doctype html><html><head>
<title>Careers | Acme Labs</title>
<meta name="description" content="Acme builds cloud software in Chandigarh.">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme Labs"}
</script></head><body>
<h1>We are hiring</h1><p>Open positions for our SaaS team in Chandigarh.</p>
<a href="/careers">See all jobs</a>
<a href="https://jobs.example-ats.test/acme">External careers</a>
<a href="mailto:careers@acme.test">Email careers</a>
<a href="mailto:jane@acme.test">Email Jane</a>
<a href="tel:+91 98765 43210">Call us</a>
</body></html>
"""


class ExtractorTests(unittest.TestCase):
    def test_extracts_verifiable_business_evidence(self):
        page = extract_page(
            HTML,
            "https://acme.test/",
            locations=["Chandigarh", "Mohali"],
            industries=["SaaS", "manufacturing"],
            hiring_terms=["we are hiring", "open positions"],
        )
        self.assertEqual("Acme Labs", page.company_name)
        self.assertEqual("Chandigarh", page.location)
        self.assertEqual("SaaS", page.industry)
        self.assertIn("careers@acme.test", page.emails)
        self.assertNotIn("jane@acme.test", page.emails)
        self.assertIn("https://acme.test/careers", page.job_urls)
        self.assertIn("https://jobs.example-ats.test/acme", page.job_urls)
        self.assertIn("+919876543210", page.phones)

    def test_combines_pages_into_one_company_lead(self):
        home = extract_page(
            HTML,
            "https://acme.test/",
            locations=["Chandigarh"],
            industries=["SaaS"],
            hiring_terms=["we are hiring"],
        )
        lead = pages_to_lead([home], "https://acme.test/", "seed")
        self.assertEqual("acme.test", lead.domain)
        self.assertEqual("https://acme.test/", lead.website_url)
        self.assertEqual(["careers@acme.test"], lead.emails)
        self.assertTrue(lead.page_hash)


if __name__ == "__main__":
    unittest.main()

