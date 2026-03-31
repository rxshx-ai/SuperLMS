"""
Moodle web-scraping client.

Handles login plus both:
    - blog entry operations (legacy flow)
    - dashboard text block operations (current flow)
on a Moodle LMS instance via its web interface.
"""

import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urljoin

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


@dataclass
class TextBlock:
    """Represents a dashboard HTML/Text block instance."""
    block_id: int
    title: str
    body: str
    block_region: str = ""
    raw_html: str = ""


# ── Client ────────────────────────────────────────────────────────────

class MoodleClient:
    """
    Interacts with a Moodle LMS via its web interface.

    Uses session-based authentication (cookies) to:
            - Log in with username/password
            - Fetch and update dashboard text blocks
            - Fetch blog entries
            - Create new blog entries
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
        logger.info("Logging in to %s as %s ...", self.base_url, self.username)

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
            "Logged in | sesskey=%s userid=%s",
            self.sesskey,
            self.userid,
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
                    logger.warning("Session expired - re-logging in ...")
                    self._logged_in = False
                    self.login()
        except requests.RequestException:
            self._logged_in = False
            self.login()

    # ── Dashboard Text Blocks ────────────────────────────────────────

    def get_dashboard_text_blocks(
        self,
        edit_mode: bool = True,
        block_region: Optional[str] = None,
    ) -> List[TextBlock]:
        """
        Fetch dashboard HTML/Text blocks from /my/.

        Args:
            edit_mode: Enable dashboard edit mode first (recommended), so
                block controls such as bui_editid links are available.
            block_region: Optional Moodle region filter (e.g. "content",
                "side-pre"). If provided, only blocks in that region
                are returned.
        """
        html = self._get_dashboard_html(edit_mode=edit_mode)
        blocks = self._parse_dashboard_text_blocks(html)
        if not block_region:
            return blocks
        return [b for b in blocks if b.block_region == block_region]

    def create_dashboard_text_block(
        self,
        title: str,
        body: str,
        block_region: str = "content",
    ) -> Optional[int]:
        """
        Create a new dashboard text block, then fill it with content.

        Uses Moodle's standard block manager flow:
          1. Open Add block picker (bui_addblock)
          2. Select Text block (bui_addblock=html)
          3. Locate newly-created block id
          4. Submit the block configuration form

        Returns the created block_id on success, else None.
        """
        self.ensure_logged_in()

        dashboard_html = self._get_dashboard_html(edit_mode=True)
        before_ids = {
            block.block_id for block in self._parse_dashboard_text_blocks(dashboard_html)
        }

        add_links = self._extract_add_block_links(dashboard_html)
        if not add_links:
            raise RuntimeError("Could not find 'Add a block' link in dashboard edit mode.")

        # Prefer adding to the main content region if that link is available.
        chosen_add_link = next(
            (u for u in add_links if f"bui_blockregion={block_region}" in u),
            add_links[0],
        )

        picker_resp = self.session.get(chosen_add_link, timeout=30)
        picker_resp.raise_for_status()

        text_block_add_url = self._extract_block_choice_url(
            picker_resp.text,
            block_name="html",
            base_url=picker_resp.url,
        )
        if not text_block_add_url:
            raise RuntimeError("Could not find text block option (bui_addblock=html).")

        add_resp = self.session.get(text_block_add_url, timeout=30, allow_redirects=True)
        add_resp.raise_for_status()

        after_html = self._get_dashboard_html(edit_mode=True)
        after_blocks = self._parse_dashboard_text_blocks(after_html)
        target_region_blocks = [
            b for b in after_blocks if b.block_region == block_region
        ]
        new_ids = [
            b.block_id for b in target_region_blocks if b.block_id not in before_ids
        ]

        if not new_ids:
            # Fallback: if region metadata was missing, detect globally.
            new_ids = [b.block_id for b in after_blocks if b.block_id not in before_ids]

        if new_ids:
            new_block_id = max(new_ids)
        else:
            # Fallback: grab the most recent "(new text block)" placeholder.
            placeholder_ids = [
                b.block_id
                for b in target_region_blocks or after_blocks
                if b.title.strip().lower() == "(new text block)"
            ]
            if placeholder_ids:
                new_block_id = max(placeholder_ids)
            else:
                logger.error("Text block add request completed but no new block id was detected.")
                return None

        if not self.update_dashboard_text_block(
            new_block_id,
            title=title,
            body=body,
            block_region=block_region,
        ):
            return None

        logger.info("Created dashboard text block #%s: %s", new_block_id, title)
        return new_block_id

    def update_dashboard_text_block(
        self,
        block_id: int,
        title: str,
        body: str,
        block_region: str = "content",
    ) -> bool:
        """
        Update an existing dashboard text block by block instance id.

        This opens /my/index.php?bui_editid=<id>, then submits the
        block_html edit form with updated title/body.
        """
        self.ensure_logged_in()

        dashboard_html = self._get_dashboard_html(edit_mode=True)
        edit_url = self._find_text_block_edit_url(dashboard_html, block_id)

        edit_resp = self.session.get(edit_url, timeout=30)
        edit_resp.raise_for_status()

        soup = BeautifulSoup(edit_resp.text, "html.parser")
        form = soup.find("form")
        if not form:
            raise RuntimeError(f"Could not find text block edit form for block #{block_id}.")

        payload = self._build_form_payload(form)
        payload["bui_editid"] = str(block_id)
        payload["config_title"] = title
        payload["config_text[text]"] = body

        # Force blocks to remain in the chosen dashboard region.
        if block_region:
            payload["bui_region"] = block_region
            payload["bui_defaultregion"] = block_region

        if not payload.get("config_text[format]"):
            payload["config_text[format]"] = "1"
        payload["submitbutton"] = payload.get("submitbutton") or "Save changes"

        action = form.get("action") or f"{self.base_url}/my/index.php"
        post_resp = self.session.post(action, data=payload, timeout=30)
        post_resp.raise_for_status()

        if "errorbox" in post_resp.text.lower() or "error" in post_resp.url.lower():
            logger.error("Failed to update dashboard text block #%s", block_id)
            return False

        logger.info("Updated dashboard text block #%s: %s", block_id, title)
        return True

    def _get_dashboard_html(self, edit_mode: bool = False) -> str:
        """Load dashboard HTML, optionally after enabling edit mode."""
        self.ensure_logged_in()
        if edit_mode:
            self._enable_dashboard_edit_mode()

        resp = self.session.get(f"{self.base_url}/my/", timeout=30)
        resp.raise_for_status()
        return resp.text

    def _enable_dashboard_edit_mode(self):
        """Enable dashboard edit mode via /editmode.php form submit."""
        self.ensure_logged_in()

        resp = self.session.get(f"{self.base_url}/my/", timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        form = soup.find("form", class_=re.compile(r"editmode-switch-form", re.I))
        if not form:
            logger.debug("Edit mode form not found on dashboard.")
            return

        switch = form.find("input", {"name": "setmode"})
        if switch and switch.has_attr("checked"):
            return

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            if (inp.get("type") or "").lower() == "checkbox":
                continue
            payload[name] = inp.get("value", "")

        payload["setmode"] = "1"
        action = form.get("action") or f"{self.base_url}/editmode.php"
        toggle_resp = self.session.post(action, data=payload, timeout=30)
        toggle_resp.raise_for_status()

    def _extract_add_block_links(self, dashboard_html: str) -> List[str]:
        """Extract dashboard Add block links (bui_addblock)."""
        soup = BeautifulSoup(dashboard_html, "html.parser")
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "bui_addblock" in href:
                links.append(urljoin(self.base_url, href))
        return links

    def _extract_block_choice_url(
        self,
        picker_html: str,
        block_name: str,
        base_url: Optional[str] = None,
    ) -> Optional[str]:
        """Find a specific block type choice URL in the Add block picker."""
        soup = BeautifulSoup(picker_html, "html.parser")
        patt = re.compile(rf"(?:\?|&)bui_addblock={re.escape(block_name)}(?:&|$)")
        resolve_base = base_url or self.base_url
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if patt.search(href):
                return urljoin(resolve_base, href)
        return None

    def _find_text_block_edit_url(self, dashboard_html: str, block_id: int) -> str:
        """Find the configure URL for a specific block id (bui_editid)."""
        soup = BeautifulSoup(dashboard_html, "html.parser")
        patt = re.compile(rf"(?:\?|&)bui_editid={block_id}(?:&|$)")
        link = soup.find("a", href=patt)
        if link and link.get("href"):
            return urljoin(self.base_url, link["href"])
        return f"{self.base_url}/my/index.php?bui_editid={block_id}"

    def _parse_dashboard_text_blocks(self, html: str) -> List[TextBlock]:
        """Parse dashboard HTML/Text blocks from /my/ HTML."""
        soup = BeautifulSoup(html, "html.parser")
        blocks: List[TextBlock] = []
        seen_ids = set()

        containers = soup.find_all(["section", "div"], attrs={"data-block": "html"})
        if not containers:
            containers = soup.find_all(["section", "div"], class_=re.compile(r"block_html", re.I))

        for container in containers:
            block = self._parse_single_text_block(container)
            if not block:
                continue
            if block.block_id in seen_ids:
                continue
            seen_ids.add(block.block_id)
            blocks.append(block)

        logger.debug("Parsed %d dashboard text block(s)", len(blocks))
        return blocks

    @staticmethod
    def _parse_single_text_block(container) -> Optional[TextBlock]:
        """Parse one dashboard text block from its HTML container."""
        block_id = None
        block_region = ""

        instance_id = container.get("data-instance-id")
        if instance_id and str(instance_id).isdigit():
            block_id = int(instance_id)
        else:
            el_id = container.get("id", "")
            m = re.search(r"(\d+)", el_id)
            if m:
                block_id = int(m.group(1))

        if block_id is None:
            return None

        region_parent = container.find_parent(attrs={"data-blockregion": True})
        if region_parent:
            block_region = (region_parent.get("data-blockregion") or "").strip()

        title = ""
        title_el = container.find(id=f"instance-{block_id}-header")
        if title_el:
            title = title_el.get_text(strip=True)
        else:
            for tag in ["h5", "h4", "h3", "h2"]:
                t = container.find(tag)
                if t:
                    title = t.get_text(strip=True)
                    break
        if not title:
            title = (container.get("aria-label") or "").strip()

        body = ""
        content_el = container.find("div", class_=re.compile(r"\bcontent\b", re.I))
        if content_el:
            no_overflow = content_el.find("div", class_=re.compile(r"no-overflow", re.I))
            if no_overflow:
                body = no_overflow.get_text("\n", strip=True)
            else:
                body = content_el.get_text("\n", strip=True)

        return TextBlock(
            block_id=block_id,
            title=title,
            body=body,
            block_region=block_region,
            raw_html=str(container),
        )

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

        logger.debug("Fetching blog entries at %s ...", url)
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

        logger.info("Created blog entry: %s", subject)
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

    @staticmethod
    def _build_form_payload(form) -> Dict[str, str]:
        """Build a payload dict from an HTML form element."""
        payload: Dict[str, str] = {}

        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue

            input_type = (inp.get("type") or "").lower()
            if input_type in {"submit", "button", "image", "file"}:
                continue

            if input_type in {"checkbox", "radio"} and not inp.has_attr("checked"):
                continue

            payload[name] = inp.get("value", "")

        for textarea in form.find_all("textarea"):
            name = textarea.get("name")
            if not name:
                continue
            payload[name] = textarea.get_text() or ""

        for select in form.find_all("select"):
            name = select.get("name")
            if not name:
                continue

            selected = select.find("option", selected=True)
            if selected:
                payload[name] = selected.get("value", "")
            else:
                first = select.find("option")
                if first:
                    payload[name] = first.get("value", "")

        return payload

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
