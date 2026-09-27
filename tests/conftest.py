import os
import sys

# tests/ imports _pokerkit_ref directly, and src/ is not installed during a
# plain `pytest` run from a checkout.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
#
# POKERFAST_TEST_INSTALLED=1 (set by cibuildwheel) tests the INSTALLED wheel
# instead: the source tree has no native library, so importing it would test
# the wrong thing.
if os.environ.get('POKERFAST_TEST_INSTALLED') != '1':
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
