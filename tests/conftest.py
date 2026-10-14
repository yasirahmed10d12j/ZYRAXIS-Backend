"""Test bootstrap: fake credentials BEFORE the app reads the environment.

No test in this suite touches a real provider API — provider calls are
monkeypatched at the app boundary (see test_rest.py / test_websocket.py).
"""
import os

os.environ.setdefault("BACKEND_API_KEYS", "test-key-1,test-key-2")
os.environ.setdefault("GEMINI_API_KEY", "fake-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "fake-groq-key")
os.environ.setdefault("PROVIDER_ORDER", "gemini,groq")
