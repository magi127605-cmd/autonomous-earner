"""
Cross-posting script for autonomous-earner
==========================================
記事をマルチプラットフォームに自動投稿する。

API対応: Dev.to, Medium, Hashnode
ブラウザ自動化: note.com, Zenn (Playwright)

使い方:
  python scripts/cross_post.py <article_path> [--platform devto,medium,hashnode]
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CREDENTIALS_FILE = STATE_DIR / "platform_keys.json"


def load_credentials() -> dict:
    """platform_keys.json から API キーを読み込む"""
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Markdown frontmatter を解析して (metadata, body) を返す"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        return {}, content

    meta_str, body = match.groups()
    meta = {}
    for line in meta_str.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().strip("'\"")
            meta[key.strip()] = value
    return meta, body


def post_to_devto(title: str, body: str, tags: list[str], canonical_url: str, api_key: str) -> dict:
    """Dev.to API で記事を投稿"""
    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "published": True,
            "tags": tags[:4],  # Dev.to max 4 tags
            "canonical_url": canonical_url,
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=data,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"success": True, "url": result.get("url", ""), "id": result.get("id")}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        return {"success": False, "error": f"HTTP {e.code}: {error_body}"}


def post_to_hashnode(title: str, body: str, tags: list[str], canonical_url: str, api_key: str, publication_id: str) -> dict:
    """Hashnode GraphQL API で記事を投稿"""
    query = """
    mutation PublishPost($input: PublishPostInput!) {
        publishPost(input: $input) {
            post {
                id
                url
            }
        }
    }
    """
    variables = {
        "input": {
            "title": title,
            "contentMarkdown": body,
            "publicationId": publication_id,
            "tags": [{"name": t} for t in tags[:5]],
            "originalArticleURL": canonical_url,
        }
    }

    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://gql.hashnode.com",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            post_data = result.get("data", {}).get("publishPost", {}).get("post", {})
            return {"success": True, "url": post_data.get("url", ""), "id": post_data.get("id")}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        return {"success": False, "error": f"HTTP {e.code}: {error_body}"}


def post_to_medium(title: str, body: str, tags: list[str], canonical_url: str, token: str) -> dict:
    """Medium API で記事を投稿"""
    # まずユーザーIDを取得
    user_req = urllib.request.Request(
        "https://api.medium.com/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(user_req) as resp:
            user_data = json.loads(resp.read().decode("utf-8"))
            user_id = user_data["data"]["id"]
    except Exception as e:
        return {"success": False, "error": f"Failed to get user ID: {e}"}

    payload = {
        "title": title,
        "contentFormat": "markdown",
        "content": f"# {title}\n\n{body}",
        "tags": tags[:5],
        "canonicalUrl": canonical_url,
        "publishStatus": "public",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.medium.com/v1/users/{user_id}/posts",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            post_data = result.get("data", {})
            return {"success": True, "url": post_data.get("url", ""), "id": post_data.get("id")}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        return {"success": False, "error": f"HTTP {e.code}: {error_body}"}


def cross_post(article_path: str, platforms: list[str] | None = None) -> dict:
    """記事をクロスポスト。結果を返す。"""
    path = Path(article_path)
    if not path.exists():
        return {"error": f"Article not found: {article_path}"}

    content = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    title = meta.get("title", path.stem)
    tags_str = meta.get("tags", "ai,tools,freelancing")
    tags = [t.strip() for t in tags_str.split(",")]

    # canonical URL
    slug = path.stem
    if re.match(r"\d{4}-\d{2}-\d{2}-", slug):
        slug = slug[11:]  # Remove date prefix
    canonical_url = f"https://magi127605-cmd.github.io/autonomous-earner/blog/{slug}/"

    creds = load_credentials()
    if not platforms:
        platforms = list(creds.keys())

    results = {}

    if "devto" in platforms and creds.get("devto", {}).get("api_key"):
        print(f"Posting to Dev.to: {title}")
        results["devto"] = post_to_devto(
            title, body, tags, canonical_url, creds["devto"]["api_key"]
        )

    if "hashnode" in platforms and creds.get("hashnode", {}).get("api_key"):
        print(f"Posting to Hashnode: {title}")
        results["hashnode"] = post_to_hashnode(
            title, body, tags, canonical_url,
            creds["hashnode"]["api_key"],
            creds["hashnode"].get("publication_id", ""),
        )

    if "medium" in platforms and creds.get("medium", {}).get("token"):
        print(f"Posting to Medium: {title}")
        results["medium"] = post_to_medium(
            title, body, tags, canonical_url, creds["medium"]["token"]
        )

    if not results:
        return {"error": "No valid platform credentials found. Create state/platform_keys.json"}

    return results


def main():
    parser = argparse.ArgumentParser(description="Cross-post articles to multiple platforms")
    parser.add_argument("article", help="Path to the markdown article file")
    parser.add_argument("--platform", help="Comma-separated platforms (devto,medium,hashnode)", default=None)
    args = parser.parse_args()

    platforms = args.platform.split(",") if args.platform else None
    results = cross_post(args.article, platforms)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
