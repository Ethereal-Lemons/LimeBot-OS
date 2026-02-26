---
name: browser
description: Control a real, local web browser to search, navigate, and extract information.
dependencies:
  python: []
  node: []
  binaries: []
---

# Web Browser 🌐
LimeBot's window to the live internet. It uses a local instance of Chrome/Chromium to interact with websites just like a human.

### Core Commands:
- `browser_navigate(url)`: Open a page. Returns the page title and a list of interactive elements with IDs (e.g., `[e12]`).
- `browser_click(element_id)`: Interact with buttons or links using the IDs from the navigation result.
- `browser_type(element_id, text)`: Fill out forms and search bars.
- `browser_scroll(direction='down')`: Move through a page to reveal more content.
- `browser_extract(selector='body')`: Get the text content of a page.
- `google_search(query)`: A shortcut to find answers on the web quickly.

### Strategy:
1. **Navigate** or **Search**.
2. **Snapshot** to see the elements.
3. **Click** or **Type** to interact.
4. **Extract** the final information needed.
