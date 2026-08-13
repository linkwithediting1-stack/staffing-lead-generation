import unittest

from leadgen_ai.config import AppConfig
from leadgen_ai.crawler import CompanyCrawler
from leadgen_ai.models import FetchResult, SearchResult


class FakeFetcher:
    def fetch(self, url: str):
        pages = {
            "https://acme.test/": """
                <html><head><title>Acme</title></head><body>
                <a href='/about'>About</a><a href='/careers'>Careers</a>
                </body></html>
            """,
            "https://acme.test/careers": """
                <html><head><title>Careers | Acme</title></head><body>
                <h1>We are hiring</h1><p>Open positions in Chandigarh for our SaaS team.</p>
                <a href='mailto:careers@acme.test'>Email us</a>
                </body></html>
            """,
            "https://acme.test/about": "<html><title>About | Acme</title><body>Cloud software.</body></html>",
        }
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text=pages[url],
        )


class CrawlerTests(unittest.TestCase):
    def test_prioritizes_careers_and_combines_company_pages(self):
        config = AppConfig()
        config.crawler.max_pages_per_domain = 3
        config.target.locations = ["Chandigarh"]
        config.target.industries = ["SaaS"]
        config.target.hiring_terms = ["we are hiring", "open positions"]
        report = CompanyCrawler(config, FakeFetcher()).crawl(
            [SearchResult(url="https://acme.test/", source="seed")]
        )
        self.assertEqual(3, report.pages_fetched)
        self.assertEqual([], report.errors)
        self.assertEqual(1, len(report.leads))
        lead = report.leads[0]
        self.assertIn("we are hiring", lead.hiring_signals)
        self.assertIn("careers@acme.test", lead.emails)
        self.assertEqual("Chandigarh", lead.location)


if __name__ == "__main__":
    unittest.main()

