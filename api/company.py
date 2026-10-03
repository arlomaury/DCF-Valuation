import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import make_handler  # noqa: E402

handler = make_handler("company")
