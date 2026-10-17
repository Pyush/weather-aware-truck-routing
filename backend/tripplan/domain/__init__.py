"""Pure domain logic.

Nothing in this package may import Django or perform I/O. That constraint is
what lets the risk rules -- 40% of the assessment -- be tested exhaustively and
instantly.
"""

from __future__ import annotations
