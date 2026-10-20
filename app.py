"""Hugging Face Spaces and Cloud Platform Entry Point.

Starts the unified Kinesia server (Frontend SPA + Realtime API + Groq Translation)
listening on 0.0.0.0 and PORT (defaults to 7860 for Hugging Face Spaces).
"""

import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure defaults for cloud platforms
if "HOST" not in os.environ:
    os.environ["HOST"] = "0.0.0.0"
if "PORT" not in os.environ:
    os.environ["PORT"] = "7860"

from backend.dashboard_server import main

if __name__ == "__main__":
    main()
