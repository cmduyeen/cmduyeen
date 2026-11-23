import sys
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import update_profile


class UpdateProfileTests(unittest.TestCase):
    def test_projects_render_minimal_markdown_by_year(self):
        repositories = [
            {"name": "older", "created_at": "2025-01-01T00:00:00Z", "html_url": "https://example.com/older"},
            {"name": "newest-november", "created_at": "2026-11-09T00:00:00Z", "html_url": "https://example.com/newest-november"},
            {"name": "older-november", "created_at": "2026-11-01T00:00:00Z", "html_url": "https://example.com/older-november"},
            {"name": "middle", "created_at": "2026-02-01T00:00:00Z", "html_url": "https://example.com/middle"},
            {"name": "profile", "created_at": "2026-12-01T00:00:00Z", "html_url": "https://example.com/profile"},
            {"name": "secret", "private": True, "created_at": "2027-01-01T00:00:00Z", "html_url": "https://example.com/secret"},
        ]

        result = update_profile.render_projects(repositories, "PROFILE")

        self.assertEqual(
            result,
            "<details>\n<summary>2026 (3)</summary>\n\n"
            "- `2026-11-09` [newest-november](https://example.com/newest-november)\n"
            "- `2026-11-01` [older-november](https://example.com/older-november)\n"
            "- `2026-02-01` [middle](https://example.com/middle)\n\n"
            "</details>\n\n"
            "<details>\n<summary>2025 (1)</summary>\n\n"
            "- `2025-01-01` [older](https://example.com/older)\n\n"
            "</details>",
        )

    def test_workflow_context_uses_repository_slug(self):
        context = update_profile.workflow_context({
            "GITHUB_REPOSITORY": "cmduyeen/cmduyeen",
        })
        self.assertEqual(context, ("cmduyeen", "cmduyeen"))

    def test_rss_entries_are_parsed_and_sorted(self):
        feed = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>Older</title><link>https://example.com/older</link><pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate></item>
          <item><title>Newer</title><link>https://example.com/newer</link><pubDate>Tue, 03 Nov 2026 09:00:00 +0000</pubDate></item>
        </channel></rss>"""

        with patch("urllib.request.urlopen", return_value=BytesIO(feed)):
            posts = update_profile.fetch_blog_posts("https://example.com/index.xml")

        self.assertEqual([post["title"] for post in posts], ["Newer", "Older"])
        rendered = update_profile.render_blog_posts(posts)
        self.assertEqual(
            rendered,
            "- `2026-11-03` [Newer](https://example.com/newer)\n"
            "- `2026-11-02` [Older](https://example.com/older)",
        )

    def test_blog_post_output_escapes_untrusted_html(self):
        post = {
            "title": '<img src=x onerror="alert(1)">',
            "url": "https://example.com/post?x=1&y=2",
            "date": datetime(2026, 11, 3, tzinfo=timezone.utc),
        }
        rendered = update_profile.render_blog_posts([post])
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", rendered)

    def test_blog_post_links_must_be_https(self):
        post = {
            "title": "Unsafe link",
            "url": "javascript:alert(1)",
            "date": datetime(2026, 11, 3, tzinfo=timezone.utc),
        }
        with self.assertRaises(ValueError):
            update_profile.render_blog_posts([post])

    def test_github_api_rejects_non_github_origins(self):
        with self.assertRaises(ValueError):
            update_profile.get_json("https://example.com/repos", "not-a-real-token")

    def test_github_api_redirects_are_rejected(self):
        handler = update_profile.RejectRedirectHandler()
        request = update_profile.urllib.request.Request("https://api.github.com/repos")
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "https://example.com/steal-token"
        ))

    def test_replace_section_requires_markers(self):
        with self.assertRaises(RuntimeError):
            update_profile.replace_section("missing", "MY-STUFF", "content")


if __name__ == "__main__":
    unittest.main()
