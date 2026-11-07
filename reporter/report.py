"""Command line: python report.py run weekly|monthly|quarterly|custom [--start D] [--end D] [--no-email]"""

from app.cli import main

if __name__ == "__main__":
    main()
