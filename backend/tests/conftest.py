"""Process-wide test configuration isolated from local secrets."""

import os

os.environ["AUTH_DISABLED"] = "true"
os.environ["BACKEND_HOST"] = "127.0.0.1"
os.environ["AUTH_TOKEN"] = "test-only-token-0123456789abcdef"
