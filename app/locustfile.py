"""
Locust load test for the RTP (Real-Time Payments) status service.

Run with:
    locust -f locustfile.py --host https://rtp.local

Then open http://localhost:8089 in your browser to control the test
(set number of users, ramp-up rate, and start/stop) via Locust's web UI.

Behavior being simulated:
  - Each virtual "user" first fetches the list of real transaction IDs
    (like a real client would query recent transactions).
  - Then it repeatedly checks the status of a MIX of transaction IDs:
      - Mostly the SAME few "hot" IDs (simulates someone repeatedly
        refreshing "is my payment done yet?") -> these become cache
        hits after the first lookup.
      - Occasionally a random "cold" ID -> simulates checking a
        transaction that hasn't been looked up recently -> cache miss.
  - A small random wait between actions simulates realistic human
    pacing, instead of hammering as fast as possible.
"""

import random
from locust import HttpUser, task, between


class RTPUser(HttpUser):
    # Each simulated user waits 1-3 seconds between actions —
    # mimics a real person checking a payment app, not a robot loop.
    wait_time = between(1, 3)

    def on_start(self):
        """
        Runs ONCE per simulated user, when they "start up" — like a
        real client fetching an initial list of transactions before
        doing anything else.
        """
        self.known_ids = []
        response = self.client.get("/txns", verify=False)
        if response.status_code == 200:
            self.known_ids = [t["id"] for t in response.json()]

        # A small pool of "hot" ids this user will repeatedly check —
        # simulates someone tracking a few specific transactions,
        # which is what makes the CACHE actually get exercised.
        if self.known_ids:
            sample_size = min(3, len(self.known_ids))
            self.hot_ids = random.sample(self.known_ids, sample_size)
        else:
            self.hot_ids = []

    @task(8)
    def check_hot_transaction(self):
        """
        80% weight: check one of this user's "hot" (repeatedly
        watched) transactions. After the first lookup, these should
        consistently be CACHE HITS.
        """
        if not self.hot_ids:
            return
        txn_id = random.choice(self.hot_ids)
        self.client.get(f"/status/{txn_id}", name="/status/[hot]", verify=False)

    @task(2)
    def check_random_transaction(self):
        """
        20% weight: check a random, less-predictable transaction —
        simulates fresh lookups. More likely to be a CACHE MISS,
        since it's not one of the repeatedly-checked "hot" ids.
        """
        if not self.known_ids:
            return
        txn_id = random.choice(self.known_ids)
        self.client.get(f"/status/{txn_id}", name="/status/[random]", verify=False)

    @task(1)
    def health_check(self):
        """Occasional health check, like a monitoring probe would do."""
        self.client.get("/health", verify=False)
