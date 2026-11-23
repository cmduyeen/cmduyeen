import email.utils
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


API_ROOT = "https://api.github.com"
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


def validate_https_url(url, field):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field} must be an HTTPS URL without embedded credentials.")
    return url


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
    validate_https_url(feed_url, "BLOG_FEED_URL")
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


def render_projects(repositories, profile_repo):
    visible = [
        repository for repository in repositories
        if not repository.get("private")
        and repository.get("name", "").casefold() != profile_repo.casefold()
    ]
    grouped = {}
    for repository in visible:
        created = parse_datetime(repository["created_at"])
        grouped.setdefault(created.year, []).append((created, repository))

    sections = []
    for year in sorted(grouped, reverse=True):
        items = sorted(
            grouped[year],
            key=lambda item: (-item[0].timestamp(), item[1]["name"].casefold()),
        )
        lines = []
        for created, repository in items:
            iso = created.date().isoformat()
            name = html.escape(repository["name"])
            url = validate_https_url(repository["html_url"], "Repository URL")
            lines.append(f"- `{iso}` [{name}]({url})")
        sections.append(
            f"<details>\n<summary>{year} ({len(items)})</summary>\n\n"
            + "\n".join(lines)
            + "\n\n</details>"
        )
    return "\n\n".join(sections) or "*No projects yet.*"


def render_blog_posts(posts, limit=5):
    lines = []
    for post in posts[:limit]:
        iso = post["date"].date().isoformat()
        url = validate_https_url(post["url"], "Blog post URL")
        title = html.escape(post["title"]).replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- `{iso}` [{title}]({url})")
    return "\n".join(lines) or "*No blog posts yet.*"


def replace_section(content, marker, value):
    start_marker = f"<!-- {marker}:START -->"
    end_marker = f"<!-- {marker}:END -->"
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError(f"README is missing valid markers for {marker}.")
    return content[:start + len(start_marker)] + "\n" + value + "\n" + content[end:]


def workflow_context(environment):
    repository_owner, separator, repository_name = environment.get(
        "GITHUB_REPOSITORY", ""
    ).partition("/")
    owner = environment.get("GITHUB_REPOSITORY_OWNER") or repository_owner
    profile_repo = environment.get("PROFILE_REPO") or repository_name
    if not separator or not owner or not profile_repo:
        raise RuntimeError("GITHUB_REPOSITORY must contain an owner and repository name.")
    return owner, profile_repo


def run():
    owner, profile_repo = workflow_context(os.environ)
    token = os.environ.get("GITHUB_TOKEN")
    feed_url = os.environ.get("BLOG_FEED_URL")
    if not token or not feed_url:
        raise RuntimeError("GITHUB_REPOSITORY, GITHUB_TOKEN, and BLOG_FEED_URL are required.")

    repositories = list_public_repositories(owner, token)
    posts = fetch_blog_posts(feed_url)

    readme_path = Path("README.md")
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_section(readme, "MY-STUFF", render_projects(repositories, profile_repo))
    readme = replace_section(readme, "BLOG-POST-LIST", render_blog_posts(posts))
    readme_path.write_text(readme, encoding="utf-8", newline="\n")
    print(f"Updated {len(repositories)} repositories and {len(posts)} RSS posts.")


if __name__ == "__main__":
    run()
