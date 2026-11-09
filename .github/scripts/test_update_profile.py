import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_profile


class UpdateProfileTests(unittest.TestCase):
    def test_projects_use_collapsible_year_totals_and_month_day_ordered_lists(self):
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
            "<details>\n<summary>2026-3R</summary>\n\n"
            "<ol>\n"
            '<li><code>NOV-09</code> -&gt; <a href="https://example.com/newest-november">newest-november</a></li>\n'
            '<li><code>NOV-01</code> -&gt; <a href="https://example.com/older-november">older-november</a></li>\n'
            '<li><code>FEB-01</code> -&gt; <a href="https://example.com/middle">middle</a></li>\n'
            "</ol>\n</details>\n\n"
            "<details>\n<summary>2025-1R</summary>\n\n"
            "<ol>\n"
            '<li><code>JAN-01</code> -&gt; <a href="https://example.com/older">older</a></li>\n'
            "</ol>\n</details>",
        )

    def test_month_range_contains_twelve_months_in_order(self):
        months = update_profile.month_range(datetime(2026, 11, 9, tzinfo=timezone.utc))

        self.assertEqual(len(months), 12)
        self.assertEqual(months[0].isoformat(), "2025-12-01")
        self.assertEqual(months[-1].isoformat(), "2026-11-01")

    def test_workflow_context_uses_repository_slug(self):
        context = update_profile.workflow_context({
            "GITHUB_REPOSITORY": "cmduyeen/cmduyeen",
        })

        self.assertEqual(context, ("cmduyeen", "cmduyeen"))

    def test_blog_publish_event_can_skip_expensive_commit_refresh(self):
        self.assertFalse(update_profile.should_update_commit_chart({
            "UPDATE_COMMIT_CHART": "false",
        }))
        self.assertTrue(update_profile.should_update_commit_chart({
            "UPDATE_COMMIT_CHART": "true",
        }))
        with self.assertRaises(ValueError):
            update_profile.should_update_commit_chart({
                "UPDATE_COMMIT_CHART": "sometimes",
            })

    def test_rss_entries_are_parsed_and_sorted(self):
        feed = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>Older</title><link>https://example.com/older</link><pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate></item>
          <item><title>Newer</title><link>https://example.com/newer</link><pubDate>Tue, 03 Nov 2026 09:00:00 +0000</pubDate></item>
        </channel></rss>"""

        from unittest.mock import patch
        from io import BytesIO

        with patch("urllib.request.urlopen", return_value=BytesIO(feed)):
            posts = update_profile.fetch_blog_posts("https://example.com/index.xml")

        self.assertEqual([post["title"] for post in posts], ["Newer", "Older"])
        rendered = update_profile.render_blog_posts(posts)
        self.assertIn('<a href="https://example.com/newer">Newer</a>', rendered)

    def test_blog_post_output_escapes_untrusted_html(self):
        post = {
            "title": '<img src=x onerror="alert(1)">',
            "url": "https://example.com/post?x=1&y=2",
            "date": datetime(2026, 11, 3, tzinfo=timezone.utc),
        }

        rendered = update_profile.render_blog_posts([post])

        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertIn("x=1&amp;y=2", rendered)

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

    def test_generated_charts_have_accessible_titles_and_twelve_ticks(self):
        months = update_profile.month_range(datetime(2026, 11, 9, tzinfo=timezone.utc))
        productivity = update_profile.render_productivity_svg(months, {}, "cmduyeen")
        trend = update_profile.render_trend_svg(months, {}, {})

        self.assertIn("<title id=\"chart-title\">Public commits by month</title>", productivity)
        self.assertIn("<desc id=\"chart-desc\">", trend)
        self.assertEqual(productivity.count("<rect "), 12)
        self.assertEqual(trend.count("<circle "), 24)

    def test_commit_activity_counts_only_recent_user_attributed_commits(self):
        months = update_profile.month_range(datetime(2026, 11, 9, tzinfo=timezone.utc))
        repositories = [
            {"name": "active", "pushed_at": "2026-11-08T00:00:00Z"},
            {"name": "old", "pushed_at": "2025-01-01T00:00:00Z"},
            {"name": "profile", "pushed_at": "2026-11-08T00:00:00Z"},
        ]
        commits = [
            {"commit": {"author": {"date": "2026-11-08T00:00:00Z"}}},
            {"commit": {"author": {"date": "2026-10-08T00:00:00Z"}}},
        ]

        from unittest.mock import patch

        with patch("update_profile.get_json", return_value=commits) as get_json:
            counts = update_profile.fetch_commit_counts(
                repositories, "cmduyeen", "profile", "token",
                months, datetime(2026, 11, 9, tzinfo=timezone.utc),
            )

        self.assertEqual(counts[months[-1]], 1)
        self.assertEqual(counts[months[-2]], 1)
        self.assertEqual(get_json.call_count, 1)
        self.assertIn("author=cmduyeen", get_json.call_args.args[0])

    def test_replace_section_requires_markers(self):
        with self.assertRaises(RuntimeError):
            update_profile.replace_section("missing", "PRODUCTIVITY", "chart")


if __name__ == "__main__":
    unittest.main()
