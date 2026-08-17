"""Keep legacy polling usable with the repository's frozen libfaketime clock."""

import time

time.sleep = lambda _seconds: None
