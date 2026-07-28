"""Legacy indexing entrypoint — disabled in MNScr.

MNScr produces editorial drafts. Search-engine notification (Google Indexing
API, IndexNow, sitemap ping, News Sitemap, recrawl, URL Inspection) belongs to
whoever publishes the content, which is the Cinerie system — never MNScr.
"""

import sys

MESSAGE = "Legacy indexing is disabled in MNScr."


def main(argv: list[str] | None = None) -> int:
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
