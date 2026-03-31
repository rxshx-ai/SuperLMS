"""Utility script to scrape Moodle dashboard text blocks.

Uses the existing MoodleClient login flow to fetch the /my/ dashboard
page and prints out candidate text blocks with their IDs, titles, and
first part of the body. This helps inspect how your customised LMS
structures dashboard text blocks so we can wire [LLMQ] logic to them.

Run:
    python scrape_dashboard_blocks.py

Requires a valid .env / config (same as agent.py).
"""

from __future__ import annotations

import textwrap

import config
from moodle_client import MoodleClient


def main():
    print("Logging in and scraping dashboard /my/ ...")
    client = MoodleClient(
        base_url=config.MOODLE_URL,
        username=config.MOODLE_USERNAME,
        password=config.MOODLE_PASSWORD,
    )
    client.login()

    blocks = client.get_dashboard_text_blocks(edit_mode=True)

    if not blocks:
        print("No candidate blocks found. You may need to adjust selectors in find_dashboard_blocks().")
        return

    print(f"Found {len(blocks)} candidate block(s):\n")
    for block in blocks:
        print(f"Block ID: {block.block_id}")
        print(f"Region  : {block.block_region!r}")
        print(f"Title   : {block.title!r}")
        snippet = textwrap.shorten(block.body or "", width=200, placeholder="...")
        print(f"Body    : {snippet}")
        print("-" * 60)


if __name__ == "__main__":
    main()
