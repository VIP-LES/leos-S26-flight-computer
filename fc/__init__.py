"""
Flight-computer package.

Adds dsdl_out to sys.path on first import so that all generated DSDL types
(leos.*, uavcan.*) are importable from anywhere inside fc.
"""

import os as _os
import sys as _sys

_dsdl_out = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "dsdl_out"))
if _os.path.isdir(_dsdl_out) and _dsdl_out not in _sys.path:
    _sys.path.insert(0, _dsdl_out)
