# Vercel's Python runtime looks for a variable named `app` in the file
# `builds`/`routes` in vercel.json point at. Every request is routed
# here and handled by the same FastAPI app you'd run locally.
#
# main.py (and database.py, routers/, utils/) live one level up, at the
# project root — add that to sys.path so the plain "from main import
# app" / "from database import get_pool" imports used throughout this
# project resolve the same way locally and on Vercel.
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app  # noqa: E402,F401

