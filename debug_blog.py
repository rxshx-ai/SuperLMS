"""Quick debug script to see the raw blog HTML."""
import config
from moodle_client import MoodleClient
config.validate()

client = MoodleClient(config.MOODLE_URL, config.MOODLE_USERNAME, config.MOODLE_PASSWORD)
client.login()

# Get raw HTML of blog page
import requests
url = f"{client.base_url}/blog/index.php"
params = {"userid": client.userid}
resp = client.session.get(url, params=params, timeout=30)

# Save the HTML for inspection
with open("debug_blog.html", "w", encoding="utf-8") as f:
    f.write(resp.text)

print(f"Saved blog HTML to debug_blog.html ({len(resp.text)} bytes)")

# Also parse and show what we currently get
entries = client.get_blog_entries()
for e in entries:
    print(f"\n--- Entry #{e.entry_id} ---")
    print(f"  Subject: {e.subject!r}")
    print(f"  Body:    {e.body!r}")
    print(f"  Author:  {e.author!r}")
