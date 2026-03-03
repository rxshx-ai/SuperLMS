from bs4 import BeautifulSoup
import re

html = open("debug_blog.html", "r", encoding="utf-8").read()
soup = BeautifulSoup(html, "html.parser")

entries = soup.find_all(class_=re.compile(r"blog.?entry", re.I))

# Just look at the first entry in detail
e = entries[0]
print(f"ID: {e.get('id')}")
print(f"Classes: {e.get('class')}")
print()

# Show the structure more deeply
def show_tree(el, depth=0):
    if hasattr(el, 'name') and el.name:
        classes = el.get('class', [])
        tag_id = el.get('id', '')
        text = el.string or ''
        if text:
            text = text.strip()[:80]
        id_str = f" id='{tag_id}'" if tag_id else ""
        cls_str = f" class={classes}" if classes else ""
        print(f"{'  '*depth}<{el.name}{id_str}{cls_str}> {text}")
        if depth < 4:  # limit depth
            for child in el.children:
                show_tree(child, depth+1)

show_tree(e)
