import argparse
import sys

try:
    from scrapling import Fetcher, StealthyFetcher

    _IMPORT_ERROR = None
except ImportError as e:
    Fetcher = None
    StealthyFetcher = None
    _IMPORT_ERROR = e


def _missing_deps_message() -> str:
    return (
        "Error: scrapling dependencies are not installed.\n"
        "Install them with:  pip install scrapling curl_cffi browserforge patchright\n"
        "Or run:  pip install -r skills/scrapling/requirements.txt"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape a webpage using Scrapling")
    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("--selector", help="CSS selector to extract", default=None)
    parser.add_argument(
        "--text", action="store_true", help="Extract text instead of HTML"
    )
    parser.add_argument(
        "--stealthy",
        action="store_true",
        help="Use StealthyFetcher to bypass anti-bot",
    )
    args = parser.parse_args()

    if _IMPORT_ERROR is not None:
        print(_missing_deps_message(), file=sys.stderr)
        print(f"Details: {_IMPORT_ERROR}", file=sys.stderr)
        return 1

    try:
        if args.stealthy:
            page = StealthyFetcher.get(args.url)
        else:
            page = Fetcher.get(args.url)
    except Exception as e:
        print(f"Error fetching {args.url}: {e}", file=sys.stderr)
        return 1

    if args.selector:
        elements = page.css(args.selector)
        if not elements:
            print(f"No elements found for selector: {args.selector}")
            return 0

        if args.text:
            print("\n".join([el.text for el in elements if el.text]))
        else:
            print("\n".join([str(el) for el in elements]))
    else:
        if args.text:
            print(page.text)
        else:
            print(page.body.html)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
