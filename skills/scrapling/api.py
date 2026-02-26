import argparse
import sys
from scrapling import Fetcher, StealthyFetcher

def main():
    parser = argparse.ArgumentParser(description="Scrape a webpage using Scrapling")
    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("--selector", help="CSS selector to extract", default=None)
    parser.add_argument("--text", action="store_true", help="Extract text instead of HTML")
    parser.add_argument("--stealthy", action="store_true", help="Use StealthyFetcher to bypass anti-bot")
    args = parser.parse_args()

    try:
        if args.stealthy:
            # Note: StealthyFetcher might need configure or use differently if it has anti-bot bypass.
            # But we fallback to Fetcher since it's simple
            page = StealthyFetcher.get(args.url)
        else:
            page = Fetcher.get(args.url)
    except Exception as e:
        print(f"Error fetching {args.url}: {e}", file=sys.stderr)
        sys.exit(1)
        
    if args.selector:
        elements = page.css(args.selector)
        if not elements:
            print(f"No elements found for selector: {args.selector}")
            sys.exit(0)
            
        if args.text:
            print("\n".join([el.text for el in elements if el.text]))
        else:
            print("\n".join([str(el) for el in elements])) # or el.html if available
    else:
        if args.text:
            print(page.text)
        else:
            print(page.body.html) # try printing body if available, or just convert to string

if __name__ == "__main__":
    main()
