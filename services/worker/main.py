# Stub. Real logic (claim job via SKIP LOCKED, run the generate->judge
# ->retry loop from the design doc §09, orchestrated with LangGraph) is
# the next piece of work. This stub just proves the container deploys
# and stays up.

import time

if __name__ == "__main__":
    print("plainly-worker stub running — no jobs processed yet")
    while True:
        time.sleep(60)
