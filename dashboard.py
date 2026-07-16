"""Compatibility wrapper for the canonical dashboard server."""

from dashboard_server import app

__all__ = ["app"]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
