from unittest.mock import MagicMock
import sys

sys.modules['curl_cffi'] = MagicMock()
sys.modules['curl_cffi.requests'] = MagicMock()
