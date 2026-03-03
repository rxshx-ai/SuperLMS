"""
Moodle web-scraping client.

Handles login, reading blog entries, and creating new blog entries
on a Moodle LMS instance via its web interface.
"""

import re
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress SSL warnings — the LMS uses an untrusted certificate
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("moodle")


# ── Data Model ────────────────────────────────────────────────────────

@dataclass
class BlogEntry:
    """Represents a single Moodle blog entry."""
    entry_id: int
    subject: str
    body: str
    author: str = ""
    timestamp: str = ""
    raw_html: str = ""


# ── Client ────────────────────────────────────────────────────────────

class MoodleClient:
    """
    Interacts with a Moodle LMS via its web interface.

    Uses session-based authentication (cookies) to:
      • Log in with username/password
      • Fetch blog entries
      • Create new blog entries
    """

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

        self.session = requests.Session()
        self.session.verify = False  # LMS uses untrusted SSL cert
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        self.sesskey: Optional[str] = None
        self.userid: Optional[int] = None
        self._logged_in = False

    # ── Authentication ────────────────────────────────────────────────

    def login(self) -> bool:
        """
        Log in to Moodle.

        1. GET /login/index.php  → extract `logintoken`
        2. POST credentials      → follow redirect to dashboard
        3. Extract `sesskey` and `userid` from the authenticated page

        Returns True on success, raises on failure.
        """
        logger.info("Logging in to %s as %s …", self.base_url, self.username)

        # Step 1 – get login page and extract the CSRF logintoken
        login_url = f"{self.base_url}/login/index.php"
        resp = self.session.get(login_url, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        token_input = soup.find("input", {"name": "logintoken"})
        logintoken = token_input["value"] if token_input else ""

        # Step 2 – POST credentials
        payload = {
            "anchor":     "",
            "logintoken": logintoken,
            "username":   self.username,
            "password":   self.password,
        }
        resp = self.session.post(login_url, data=payload, timeout=30)
        resp.raise_for_status()

        # Step 3 – verify login succeeded by checking for sesskey
        self.sesskey = self._extract_sesskey(resp.text)
        if not self.sesskey:
            # Some Moodle setups redirect; try fetching the dashboard
            dash = self.session.get(f"{self.base_url}/my/", timeout=30)
            self.sesskey = self._extract_sesskey(dash.text)

        if not self.sesskey:
            raise RuntimeError(
                "Login failed – could not extract sesskey. "
                "Check username/password or whether the site is up."
            )

        self.userid = self._extract_userid(resp.text)
        if not self.userid:
            dash_text = self.session.get(
                f"{self.base_url}/my/", timeout=30
            ).text
            self.userid = self._extract_userid(dash_text)

        self._logged_in = True
        logger.info(
            "✅  Logged in  |  sesskey=%s  userid=%s",
            self.sesskey, self.userid,
        )
        return True

    def ensure_logged_in(self):
        """Re-login if session expired."""
        if not self._logged_in:
            self.login()
            return

        # Quick check: hit a lightweight page and look for sesskey
        try:
            resp = self.session.get(
                f"{self.base_url}/my/", timeout=15, allow_redirects=False
            )
            if resp.status_code in (301, 302, 303):
                location = resp.headers.get("Location", "")
                if "login" in location:
                    logger.warning("Session expired — re-logging in …")
                    self._logged_in = False
                    self.login()
        except requests.RequestException:
            self._logged_in = False
            self.login()

    # ── Reading Blog Entries ──────────────────────────────────────────

    def get_blog_entries(self, userid: Optional[int] = None) -> List[BlogEntry]:
        """
        Fetch blog entries for a given user (default: self).

        Returns a list of BlogEntry objects parsed from the blog page HTML.
        """
        self.ensure_logged_in()
        uid = userid or self.userid

        url = f"{self.base_url}/blog/index.php"
        params = {}
        if uid:
            params["userid"] = uid

        logger.debug("Fetching blog entries at %s …", url)
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()

        return self._parse_blog_entries(resp.text)

    # ── Creating Blog Entries ─────────────────────────────────────────

    def create_blog_entry(
        self,
        subject: str,
        body: str,
        publish_state: str = "site",
    ) -> bool:
        """
        Create a new blog entry.

        1. GET  /blog/edit.php?action=add  → get form + hidden fields
        2. POST /blog/edit.php             → submit the entry

        Args:
            subject:       entry title
            body:          entry body (HTML)
            publish_state: 'site', 'public', or 'draft'
        """
        self.ensure_logged_in()

        # Step 1 – load the add-entry form to get hidden fields
        form_url = f"{self.base_url}/blog/edit.php"
        resp = self.session.get(
            form_url, params={"action": "add"}, timeout=30
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract hidden fields
        sesskey = self._form_field(soup, "sesskey") or self.sesskey
        qf_marker = self._form_field(soup, "_qf__blog_edit_form") or "1"
        item_id = self._form_field(soup, "summary_editor[itemid]") or ""

        # If itemid is in an element with a different name pattern, search deeper
        if not item_id:
            item_el = soup.find("input", {"name": re.compile(r"itemid")})
            if item_el:
                item_id = item_el.get("value", "")

        # Step 2 – build payload and POST
        payload = {
            "sesskey":                  sesskey,
            "_qf__blog_edit_form":      qf_marker,
            "subject":                  subject,
            "summary_editor[text]":     body,
            "summary_editor[format]":   "1",       # 1 = HTML
            "summary_editor[itemid]":   item_id,
            "publishstate":             publish_state,
            "tags":                     "",
            "action":                   "add",
            "entryid":                  "0",
            "modid":                    "0",
            "courseid":                 "0",
            "submitbutton":             "Save changes",
        }

        resp = self.session.post(form_url, data=payload, timeout=30)
        resp.raise_for_status()

        # Check for success (no error box present)
        if "errorbox" in resp.text.lower() or "error" in resp.url.lower():
            logger.error("Failed to create blog entry: %s", subject)
            return False

        logger.info("📝  Created blog entry: %s", subject)
        return True

    # ── Private Helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_sesskey(html: str) -> Optional[str]:
        """Pull sesskey from page HTML."""
        # Pattern 1: "sesskey":"abc123"
        m = re.search(r'"sesskey"\s*:\s*"([a-zA-Z0-9]+)"', html)
        if m:
            return m.group(1)
        # Pattern 2: <input name="sesskey" value="abc123">
        soup = BeautifulSoup(html, "html.parser")
        inp = soup.find("input", {"name": "sesskey"})
        if inp:
            return inp.get("value")
        # Pattern 3: sesskey=abc123 in a URL
        m = re.search(r'sesskey=([a-zA-Z0-9]+)', html)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_userid(html: str) -> Optional[int]:
        """Pull the logged-in user's ID from page HTML."""
        # Pattern 1: data-userid="123"
        m = re.search(r'data-userid="(\d+)"', html)
        if m:
            return int(m.group(1))
        # Pattern 2: /user/profile.php?id=123
        m = re.search(r'/user/profile\.php\?id=(\d+)', html)
        if m:
            return int(m.group(1))
        # Pattern 3: "userid":123
        m = re.search(r'"userid"\s*:\s*(\d+)', html)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _form_field(soup: BeautifulSoup, name: str) -> Optional[str]:
        """Get the value of a form input by name."""
        el = soup.find("input", {"name": name})
        if el:
            return el.get("value", "")
        # Also check select elements
        el = soup.find("select", {"name": name})
        if el:
            selected = el.find("option", selected=True)
            if selected:
                return selected.get("value", "")
        return None

    def _parse_blog_entries(self, html: str) -> List[BlogEntry]:
        """
        Parse blog entries from Moodle blog HTML.

        Handles multiple Moodle themes / versions by trying several
        CSS selector patterns.
        """
        soup = BeautifulSoup(html, "html.parser")
        entries: List[BlogEntry] = []

        # Strategy 1: Modern Moodle 4.x — <article> or <div> with class 'blog_entry'
        containers = soup.find_all(["article", "div"], class_=re.compile(r"blog.?entry", re.I))

        if not containers:
            # Strategy 2: look for any element with id starting with 'entry-' or 'b'
            containers = soup.find_all(id=re.compile(r'^(entry-|b)\d+'))

        if not containers:
            # Strategy 3: look for the blog post listing wrapper
            wrapper = soup.find(class_=re.compile(r"blog.?posts", re.I))
            if wrapper:
                containers = wrapper.find_all(["article", "div"], recursive=False)

        for container in containers:
            entry = self._parse_single_entry(container)
            if entry:
                entries.append(entry)

        logger.debug("Parsed %d blog entries", len(entries))
        return entries

    @staticmethod
    def _parse_single_entry(container) -> Optional[BlogEntry]:
        """Parse a single blog entry from its HTML container."""
        # ── Extract entry ID ──
        entry_id = None
        el_id = container.get("id", "")
        m = re.search(r'(\d+)', el_id)
        if m:
            entry_id = int(m.group(1))
        else:
            # Look for links containing entryid=
            link = container.find("a", href=re.compile(r'entryid=(\d+)'))
            if link:
                m = re.search(r'entryid=(\d+)', link["href"])
                if m:
                    entry_id = int(m.group(1))

        if entry_id is None:
            return None

        # ── Extract subject / title ──
        subject = ""
        for tag in ["h3", "h4", "h2"]:
            title_el = container.find(tag)
            if title_el:
                subject = title_el.get_text(strip=True)
                break
        if not subject:
            title_el = container.find(class_=re.compile(r"title|subject", re.I))
            if title_el:
                subject = title_el.get_text(strip=True)

        # ── Extract body ──
        body = ""
        # Moodle structure: div.content > [div.audience, div.no-overflow (BODY), div.commands, ...]
        content_el = container.find(class_="content")
        if content_el:
            # Look for the inner no-overflow div that holds the actual body
            # (Skip the outer one that IS the content div)
            inner_divs = content_el.find_all("div", class_="no-overflow", recursive=False)
            for div in inner_divs:
                # Skip if this div has 'content' in its classes (it's the wrapper)
                div_classes = div.get("class", [])
                if "content" in div_classes:
                    continue
                text = div.get_text(strip=True)
                if text:
                    body = text
                    break

            # Fallback: try to find any direct text-bearing child that isn't metadata
            if not body:
                skip_classes = {"audience", "commands", "comment-ctrl",
                                "comment-link", "showcommentsnonjs",
                                "mdl-left", "side", "options"}
                for child in content_el.children:
                    if not hasattr(child, 'get'):
                        continue
                    child_classes = set(child.get("class", []))
                    if child_classes & skip_classes:
                        continue
                    text = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
                    if text and len(text) > 0:
                        body = text
                        break

        # Final fallback: get paragraph text
        if not body:
            body_el = container.find(class_=re.compile(r"summary|body|description", re.I))
            if body_el:
                body = body_el.get_text(strip=True)
            else:
                paragraphs = container.find_all("p")
                body = "\n".join(p.get_text(strip=True) for p in paragraphs)

        # ── Extract author ──
        author = ""
        author_el = container.find(class_=re.compile(r"author|user", re.I))
        if author_el:
            author = author_el.get_text(strip=True)

        # ── Extract timestamp ──
        timestamp = ""
        time_el = container.find("time") or container.find(
            class_=re.compile(r"date|time|created", re.I)
        )
        if time_el:
            timestamp = time_el.get_text(strip=True)

        return BlogEntry(
            entry_id=entry_id,
            subject=subject,
            body=body,
            author=author,
            timestamp=timestamp,
            raw_html=str(container),
        )
