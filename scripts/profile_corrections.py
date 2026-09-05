#!/usr/bin/env python3
"""Run the Phase 4 pipeline and publish the correction structure profile."""

# The production scan and the lineage resolver intentionally share one streaming
# pass. This entrypoint keeps the requested profiling command discoverable while
# guaranteeing that profiling cannot drift from the resolver's extractor.
from resolve_corrections import main


if __name__ == "__main__":
    raise SystemExit(main())
