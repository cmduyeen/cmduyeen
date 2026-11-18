import calendar
import email.utils
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


API_ROOT = "https://api.github.com"
MONTH_COUNT = 12
PAGE_SIZE = 100
MAX_FEED_BYTES = 2 * 1024 * 1024


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def parse_datetime(value):
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_json(url, token):
    if urllib.parse.urlparse(url).netloc != "api.github.com":
        raise ValueError("GitHub API requests must remain on api.github.com")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        opener = urllib.request.build_opener(RejectRedirectHandler)
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"GitHub API request failed ({error.code}) for "
            f"{urllib.parse.urlparse(url).path}: {details}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error.reason}") from error


def list_public_repositories(owner, token):
    repositories = []
    page = 1
    while True:
        query = urllib.parse.urlencode({
            "type": "public",
            "sort": "created",
            "direction": "desc",
            "per_page": PAGE_SIZE,
            "page": page,
        })
        result = get_json(f"{API_ROOT}/users/{urllib.parse.quote(owner)}/repos?{query}", token)
        if not isinstance(result, list):
            raise RuntimeError("GitHub returned an unexpected repository list.")
        repositories.extend(repository for repository in result if not repository.get("private"))
        if len(result) < PAGE_SIZE:
            return repositories
        page += 1


def fetch_blog_posts(feed_url):
    parsed_url = urllib.parse.urlparse(feed_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username or parsed_url.password:
        raise ValueError("BLOG_FEED_URL must be an HTTPS URL without embedded credentials.")

    request = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "profile-readme-generator", "Accept": "application/rss+xml, application/atom+xml, application/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            feed_content = response.read(MAX_FEED_BYTES + 1)
            if len(feed_content) > MAX_FEED_BYTES:
                raise RuntimeError("Blog feed exceeds the 2 MiB size limit.")
            root = ET.fromstring(feed_content)
    except (urllib.error.URLError, ET.ParseError) as error:
        raise RuntimeError(f"Unable to read blog feed: {error}") from error

    entries = [
        element for element in root.iter()
        if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
    ]
    posts = []
    for index, entry in enumerate(entries, start=1):
        fields = {}
        for element in entry.iter():
            name = element.tag.rsplit("}", 1)[-1].lower()
            if name in {"title", "pubdate", "published", "updated", "date"} and name not in fields:
                fields[name] = "".join(element.itertext()).strip()
            if name == "link" and "link" not in fields:
                fields["link"] = element.get("href") or "".join(element.itertext()).strip()

        title = fields.get("title")
        link = fields.get("link")
        published = next(
            (fields[name] for name in ("pubdate", "published", "updated", "date") if fields.get(name)),
            None,
        )
        if not title or not link or not published:
            raise RuntimeError(f"Blog feed entry {index} is missing a title, link, or publication date.")
        link = validate_https_url(link, f"Blog feed entry {index} link")
        posts.append({"title": title, "url": link, "date": parse_datetime(published)})

    return sorted(posts, key=lambda post: post["date"], reverse=True)


def validate_https_url(url, field):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field} must be an HTTPS URL without embedded credentials.")
    return url


def month_range(now):
    current = now.astimezone(timezone.utc).date().replace(day=1)
    months = []
    for offset in range(MONTH_COUNT - 1, -1, -1):
        month_index = current.year * 12 + current.month - 1 - offset
        year, zero_based_month = divmod(month_index, 12)
        month = zero_based_month + 1
        months.append(date(year, month, 1))
    return months


def render_year_summary_svg(year, count):
    label = f"{year} HARVEST"
    badge = f"{count} REPOS"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="340" height="34" viewBox="0 0 340 34" role="img" '
        f'aria-label="{year} - {count} repositories">'
        '<rect x="1" y="1" width="338" height="32" rx="6" fill="#fff8e7" stroke="#8f563b" stroke-width="1.8"/>'
        '<rect x="8" y="7" width="112" height="20" rx="4" fill="#5b3a29"/>'
        f'<text x="64" y="21" text-anchor="middle" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="11" font-weight="700" fill="#f7eed6">{html.escape(label)}</text>'
        '<rect x="128" y="7" width="88" height="20" rx="4" fill="#4f8f3c"/>'
        f'<text x="172" y="21" text-anchor="middle" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="11" font-weight="700" fill="#f7eed6">{html.escape(badge)}</text>'
        '<text x="326" y="21" text-anchor="end" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10.5" fill="#8f563b">Click to expand</text>'
        '</svg>\n'
    )


def render_repo_card_svg(created_label, name):
    display_name = name if len(name) <= 30 else name[:27] + "..."
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 36" role="img" '
        f'aria-label="{html.escape(created_label + ": " + name, quote=True)}">'
        '<rect x="1" y="1" width="408" height="34" rx="6" fill="#fff8e7" stroke="#8f563b" stroke-width="1.6"/>'
        '<rect x="8" y="7" width="58" height="22" rx="4" fill="#5b3a29"/>'
        f'<text x="37" y="22" text-anchor="middle" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10.5" font-weight="700" fill="#f7eed6">{html.escape(created_label)}</text>'
        f'<text x="76" y="22" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="12" font-weight="700" fill="#2d241c">{html.escape(display_name)}</text>'
        '<text x="394" y="22" text-anchor="end" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="12" font-weight="700" fill="#4f8f3c">↗</text>'
        '</svg>\n'
    )


def write_year_summary_svgs(repositories, profile_repo, assets_dir):
    visible = [
        repository for repository in repositories
        if not repository.get("private")
        and repository.get("name", "").casefold() != profile_repo.casefold()
    ]
    grouped = {}
    for repository in visible:
        created = parse_datetime(repository["created_at"])
        grouped.setdefault(created.year, []).append((created, repository))
    for year, items in grouped.items():
        (assets_dir / f"year-{year}.svg").write_text(
            render_year_summary_svg(year, len(items)), encoding="utf-8"
        )
        sorted_items = sorted(
            items, key=lambda item: (-item[0].timestamp(), item[1]["name"].casefold())
        )
        for index, (created, repository) in enumerate(sorted_items, start=1):
            created_label = f"{calendar.month_abbr[created.month].upper()}-{created.day:02d}"
            (assets_dir / f"repo-{year}-{index:02d}.svg").write_text(
                render_repo_card_svg(created_label, repository["name"]),
                encoding="utf-8",
            )


def render_projects(repositories, profile_repo):
    visible = [
        repository for repository in repositories
        if not repository.get("private")
        and repository.get("name", "").casefold() != profile_repo.casefold()
    ]
    visible.sort(key=lambda repository: (
        -parse_datetime(repository["created_at"]).timestamp(),
        repository["name"].casefold(),
    ))

    grouped = {}
    for repository in visible:
        created = parse_datetime(repository["created_at"])
        grouped.setdefault(created.year, []).append((created, repository))

    sections = []
    for year in sorted(grouped, reverse=True):
        entries = []
        for index, (created, repository) in enumerate(
            sorted(
                grouped[year],
                key=lambda item: (-item[0].timestamp(), item[1]["name"].casefold()),
            ),
            start=1,
        ):
            created_label = f"{calendar.month_abbr[created.month].upper()}-{created.day:02d}"
            name = html.escape(repository["name"], quote=True)
            url = html.escape(repository["html_url"], quote=True)
            entries.append(
                f'<a href="{url}"><img src="assets/repo-{year}-{index:02d}.svg" alt="{created_label}: {name}" width="49%" /></a>'
            )
        details = (
            f"<details>\n<summary><img src=\"assets/year-{year}.svg\" alt=\"{year}-{len(grouped[year])}R\" align=\"absmiddle\" /></summary>\n\n"
            "<p>\n" + "\n".join(entries) + "\n</p>\n</details>"
        )
        sections.append(details)
    return "\n\n".join(sections) or "*No projects yet.*"


def render_blog_post_svg(post):
    iso = post["date"].date().isoformat()
    title = post["title"]
    if len(title) > 76:
        title = title[:73].rstrip() + "..."
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 42" role="img" '
        f'aria-label="{html.escape(iso + ": " + post["title"], quote=True)}">'
        '<rect x="1" y="1" width="838" height="40" rx="7" fill="#fff8e7" stroke="#8f563b" stroke-width="1.8"/>'
        '<rect x="10" y="9" width="92" height="24" rx="4" fill="#4f8f3c"/>'
        f'<text x="56" y="25" text-anchor="middle" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="11.5" font-weight="700" fill="#f7eed6">{html.escape(iso)}</text>'
        f'<text x="116" y="25" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" font-size="13.5" font-weight="600" fill="#2d241c">{html.escape(title)}</text>'
        '<text x="820" y="26" text-anchor="end" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="14" font-weight="700" fill="#8f563b">→</text>'
        '</svg>\n'
    )


def write_blog_post_svgs(posts, assets_dir, limit=5):
    for index, post in enumerate(posts[:limit], start=1):
        (assets_dir / f"post-{index}.svg").write_text(
            render_blog_post_svg(post), encoding="utf-8"
        )


def render_blog_posts(posts, limit=5):
    lines = []
    for index, post in enumerate(posts[:limit], start=1):
        iso = post["date"].date().isoformat()
        url = html.escape(validate_https_url(post["url"], "Blog post URL"), quote=True)
        alt = html.escape(f"{iso}: {post['title']}", quote=True)
        lines.append(
            f'<a href="{url}"><img src="assets/post-{index}.svg" alt="{alt}" width="100%" /></a>'
        )
    return "\n".join(lines) or "*No blog posts yet.*"


def replace_section(content, marker, value):
    start_marker = f"<!-- {marker}:START -->"
    end_marker = f"<!-- {marker}:END -->"
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError(f"README is missing valid markers for {marker}.")
    return content[:start + len(start_marker)] + "\n" + value + "\n" + content[end:]


def fetch_commit_counts(repositories, owner, profile_repo, token, months, now):
    start = datetime.combine(months[0], datetime.min.time(), timezone.utc)
    counts = Counter()
    for repository in repositories:
        if repository.get("name", "").casefold() == profile_repo.casefold():
            continue
        pushed_at = repository.get("pushed_at")
        if not pushed_at or parse_datetime(pushed_at) < start:
            continue

        page = 1
        while True:
            query = urllib.parse.urlencode({
                "author": owner,
                "since": start.isoformat().replace("+00:00", "Z"),
                "until": now.isoformat().replace("+00:00", "Z"),
                "per_page": PAGE_SIZE,
                "page": page,
            })
            url = (
                f"{API_ROOT}/repos/{urllib.parse.quote(owner)}/"
                f"{urllib.parse.quote(repository['name'])}/commits?{query}"
            )
            commits = get_json(url, token)
            if not isinstance(commits, list):
                raise RuntimeError(f"GitHub returned unexpected commit data for {repository['name']}.")
            for item in commits:
                commit_date = item.get("commit", {}).get("author", {}).get("date")
                if not commit_date:
                    commit_date = item.get("commit", {}).get("committer", {}).get("date")
                if commit_date:
                    counts[parse_datetime(commit_date).date().replace(day=1)] += 1
            if len(commits) < PAGE_SIZE:
                break
            page += 1
    return counts


def svg_document(title, description, body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="270" '
        'viewBox="0 0 760 270" role="img" aria-labelledby="chart-title chart-desc">'
        f"<title id=\"chart-title\">{html.escape(title)}</title>"
        f"<desc id=\"chart-desc\">{html.escape(description)}</desc>"
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}'
        '.axis{fill:#6e523f;font-size:11px}.grid{stroke:#e2d4b7;stroke-width:1}'
        '.caption{fill:#2d241c;font-size:15px;font-weight:700}'
        '.value{fill:#5b3a29;font-size:10px;font-weight:600;text-anchor:middle}</style>'
        '<rect x="1" y="1" width="758" height="268" rx="10" fill="#f7eed6" stroke="#8f563b" stroke-width="2"/>'
        f"{body}</svg>\n"
    )


def month_label(value):
    return f"{calendar.month_abbr[value.month]} '{value.year % 100:02d}"


def chart_grid(values, left, top, width, height):
    maximum = max(values, default=0)
    step = max(1, (maximum + 3) // 4)
    axis_max = step * 4
    elements = []
    for index in range(5):
        value = axis_max - index * step
        y = top + height * index / 4
        elements.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + width}" y2="{y:.1f}"/>')
        elements.append(f'<text class="axis" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{value}</text>')
    return elements, axis_max


def render_productivity_svg(months, counts, owner):
    left, top, width, height = 58, 46, 680, 158
    values = [counts.get(month, 0) for month in months]
    elements, axis_max = chart_grid(values, left, top, width, height)
    slot = width / len(months)
    bar_width = min(28, slot * 0.62)
    for index, (month, value) in enumerate(zip(months, values)):
        bar_height = height * value / axis_max
        x = left + slot * index + (slot - bar_width) / 2
        y = top + height - bar_height
        elements.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="#4f8f3c"/>'
        )
        elements.append(f'<text class="value" x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}">{value}</text>')
        elements.append(
            f'<text class="axis" x="{left + slot * index + slot / 2:.1f}" y="{top + height + 24}" '
            f'text-anchor="middle">{month_label(month)}</text>'
        )
    elements.append('<text class="caption" x="18" y="25">Public commits by month</text>')
    description = (
        f"GitHub-attributed commits by {owner} on the default branches of public "
        "repositories over the last 12 months."
    )
    return svg_document("Public commits by month", description, "".join(elements))


def render_trend_svg(months, repository_counts, blog_counts):
    left, top, width, height = 58, 48, 680, 156
    repo_values = [repository_counts.get(month, 0) for month in months]
    blog_values = [blog_counts.get(month, 0) for month in months]
    elements, axis_max = chart_grid(repo_values + blog_values, left, top, width, height)

    def series(values, color):
        points = []
        for index, value in enumerate(values):
            x = left + width * index / max(1, len(months) - 1)
            y = top + height - height * value / axis_max
            points.append((x, y))
        path = " ".join(
            f"{'M' if index == 0 else 'L'}{point[0]:.1f},{point[1]:.1f}"
            for index, point in enumerate(points)
        )
        output = [f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>']
        output.extend(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>'
            for x, y in points
        )
        return "".join(output)

    elements.append(series(repo_values, "#4f8f3c"))
    elements.append(series(blog_values, "#d97706"))
    for index, month in enumerate(months):
        x = left + width * index / max(1, len(months) - 1)
        elements.append(
            f'<text class="axis" x="{x:.1f}" y="{top + height + 24}" text-anchor="middle">'
            f"{month_label(month)}</text>"
        )
    elements.extend([
        '<line x1="533" y1="25" x2="551" y2="25" stroke="#4f8f3c" stroke-width="2.5"/>'
        '<text class="axis" x="557" y="29">New repos</text>',
        '<line x1="634" y1="25" x2="652" y2="25" stroke="#d97706" stroke-width="2.5"/>'
        '<text class="axis" x="658" y="29">Blog posts</text>',
    ])
    elements.append('<text class="caption" x="18" y="25">New repositories and blog posts</text>')
    description = "New public repositories and RSS blog posts published per month over the last 12 months."
    return svg_document("New repositories and blog posts", description, "".join(elements))


def workflow_context(environment):
    repository_owner, separator, repository_name = environment.get(
        "GITHUB_REPOSITORY", ""
    ).partition("/")
    owner = environment.get("GITHUB_REPOSITORY_OWNER") or repository_owner
    profile_repo = environment.get("PROFILE_REPO") or repository_name
    if not separator or not owner or not profile_repo:
        raise RuntimeError("GITHUB_REPOSITORY must contain an owner and repository name.")
    return owner, profile_repo


def should_update_commit_chart(environment):
    value = environment.get("UPDATE_COMMIT_CHART", "true").lower()
    if value not in {"true", "false"}:
        raise ValueError("UPDATE_COMMIT_CHART must be either true or false.")
    return value == "true"


def run():
    owner, profile_repo = workflow_context(os.environ)
    token = os.environ.get("GITHUB_TOKEN")
    feed_url = os.environ.get("BLOG_FEED_URL")
    if not token or not feed_url:
        raise RuntimeError("GITHUB_REPOSITORY, GITHUB_TOKEN, and BLOG_FEED_URL are required.")

    now = datetime.now(timezone.utc)
    months = month_range(now)
    repositories = list_public_repositories(owner, token)
    visible_repositories = [
        repository for repository in repositories
        if repository.get("name", "").casefold() != profile_repo.casefold()
    ]
    posts = fetch_blog_posts(feed_url)
    update_commit_chart = should_update_commit_chart(os.environ)
    if update_commit_chart:
        commit_counts = fetch_commit_counts(visible_repositories, owner, profile_repo, token, months, now)
    elif not Path("assets/productivity.svg").is_file():
        raise RuntimeError("Cannot skip commit chart refresh because assets/productivity.svg is missing.")

    repo_counts = Counter()
    for repository in visible_repositories:
        created = parse_datetime(repository["created_at"]).date().replace(day=1)
        repo_counts[created] += 1
    blog_counts = Counter(
        post["date"].date().replace(day=1)
        for post in posts
    )

    readme_path = Path("README.md")
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_section(readme, "MY-STUFF", render_projects(repositories, profile_repo))
    readme = replace_section(readme, "BLOG-POST-LIST", render_blog_posts(posts))
    productivity_section = (
        "![Public commits by month](assets/productivity.svg)"
        "\n\n![New repositories and blog posts per month](assets/repo-blog-trend.svg)"
    )
    readme = replace_section(readme, "PRODUCTIVITY", productivity_section)
    readme_path.write_text(readme, encoding="utf-8", newline="\n")

    assets = Path("assets")
    assets.mkdir(exist_ok=True)
    write_year_summary_svgs(repositories, profile_repo, assets)
    write_blog_post_svgs(posts, assets)
    if update_commit_chart:
        (assets / "productivity.svg").write_text(
            render_productivity_svg(months, commit_counts, owner), encoding="utf-8", newline="\n"
        )
    (assets / "repo-blog-trend.svg").write_text(
        render_trend_svg(months, repo_counts, blog_counts), encoding="utf-8", newline="\n"
    )
    print(
        f"Updated {len(visible_repositories)} public repositories and "
        f"{len(posts)} RSS posts; "
        f"{'refreshed' if update_commit_chart else 'retained'} the commit chart."
    )


if __name__ == "__main__":
    run()
