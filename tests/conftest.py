import os
import sys

# tests/ imports _pokerkit_ref directly, and src/ is not installed during a
# plain `pytest` run from a checkout.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
