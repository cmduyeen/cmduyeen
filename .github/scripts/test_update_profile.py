import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_profile


class UpdateProfileTests(unittest.TestCase):
    def test_projects_are_grouped_by_year_month_and_numbered_within_month(self):
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
            "### 2026\n"
            "#### Nov\n"
            "`T11-1` -> [newest-november](https://example.com/newest-november)\n"
            "`T11-2` -> [older-november](https://example.com/older-november)\n\n"
            "#### Feb\n"
            "`T02-1` -> [middle](https://example.com/middle)\n\n"
            "### 2025\n"
            "#### Jan\n"
            "`T01-1` -> [older](https://example.com/older)",
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
