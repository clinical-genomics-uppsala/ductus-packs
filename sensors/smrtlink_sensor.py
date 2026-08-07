import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.smrt_client import SMRTClient

# Subclasses PollingSensor: StackStorm's sensor plugin loader requires
# run/add_trigger/update_trigger/remove_trigger, which plain `object` doesn't
# provide. Falls back to a minimal stand-in when st2reactor isn't installed
# (e.g. local pytest runs, which stub st2common but not st2reactor).
try:
    from st2reactor.sensor.base import PollingSensor
except ImportError:
    class PollingSensor:
        def __init__(self, sensor_service, config=None, poll_interval=5):
            self._sensor_service = sensor_service
            self.sensor_service = sensor_service
            self._config = config or {}
            self.config = self._config
            self._poll_interval = poll_interval

        def run(self):
            pass

POLL_INTERVAL = 600      # seconds
RUN_MAX_AGE_DAYS = 30    # ignore runs older than this on startup
STATE_KEY = "smrtlink.run_state"


class SMRTLinkSensor(PollingSensor):

    def __init__(self, sensor_service, config=None, poll_interval=None):
        super().__init__(
            sensor_service=sensor_service,
            config=config,
            poll_interval=poll_interval or POLL_INTERVAL,
        )
        self._sensor_service = sensor_service
        self._logger = sensor_service.get_logger(__name__)
        self._config = config or {}
        self._client = None

    # ── StackStorm lifecycle ───────────────────────────────────

    def setup(self):
        # get_value() defaults local=True (namespaced key); local=False reads
        # the bare global key that `st2 key set smrtlink.username ...` writes.
        self._client = SMRTClient(
            base_url=self._config.get("base_url"),
            username=self._sensor_service.get_value("smrtlink.username", local=False, decrypt=True),
            password=self._sensor_service.get_value("smrtlink.password", local=False, decrypt=True),
            ssl_verify=self._config.get("ssl_verify", False),
        )

    def poll(self):
        state = self._load_state()

        runs = self._get_runs()
        if runs is None:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=RUN_MAX_AGE_DAYS)

        for run in runs:
            uid = run.get("uniqueId")
            created_str = run.get("createdAt", "")

            # skip runs older than cutoff (avoids replaying history on first boot)
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if created < cutoff:
                    continue
            except ValueError:
                pass

            # new run — add to state store
            if uid not in state:
                state[uid] = {
                    "name": run.get("name"),
                    "triggered": False,
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                }
                self._logger.info(f"New run detected: {run.get('name')} ({uid})")

            # already triggered — nothing to do
            if state[uid]["triggered"]:
                continue

            # check if all collections are complete
            if self._all_collections_complete(uid):
                self._logger.info(f"Run complete, firing trigger: {state[uid]['name']}")
                if self._dispatch_trigger(run):
                    state[uid]["triggered"] = True

        self._save_state(state)

    def cleanup(self):
        pass

    def add_trigger(self, trigger):
        pass

    def update_trigger(self, trigger):
        pass

    def remove_trigger(self, trigger):
        pass

    # ── SMRT Link API ──────────────────────────────────────────

    def _get_runs(self):
        try:
            return self._client.get("/smrt-link/runs")
        except Exception as e:
            self._logger.error(f"Failed to fetch runs: {e}")
            return None

    def _all_collections_complete(self, run_uuid):
        try:
            collections = self._client.get(f"/smrt-link/runs/{run_uuid}/collections")
        except Exception as e:
            self._logger.error(f"Failed to fetch collections for {run_uuid}: {e}")
            return False

        if not collections:
            return False

        return all(c.get("status") == "Complete" for c in collections)

    def _get_uniform_ccs_mode(self, collections):
        """Returns (mode, True) if all collections share one ccsExecutionMode, else (modes_set, False)."""
        if not collections:
            return None, True
        modes = {c.get("ccsExecutionMode") for c in collections}
        if len(modes) > 1:
            return modes, False
        return modes.pop(), True

    # ── Trigger ────────────────────────────────────────────────

    def _dispatch_trigger(self, run):
        try:
            collections = self._client.get(f"/smrt-link/runs/{run['uniqueId']}/collections")
        except Exception as e:
            self._logger.error(f"Could not fetch collections for trigger payload: {e}")
            collections = []

        mode, uniform = self._get_uniform_ccs_mode(collections)
        if not uniform:
            self._logger.error(
                "Run %s has mixed ccsExecutionMode values %s — skipping (operator must investigate)",
                run["name"],
                mode,
            )
            return False

        trigger_name = (
            "ductus.pacbio_run_complete"
            if mode == "OnInstrument"
            else "ductus.pacbio_run_complete_longplex"
        )
        payload = {
            "run_uuid": run.get("uniqueId"),
            "run_name": run.get("name"),
            "collection_paths": [c.get("collectionPathUri") for c in collections],
            "collections": collections,
        }
        self._sensor_service.dispatch(
            trigger=trigger_name,
            payload=payload,
        )
        self._logger.info(
            "Dispatched %s for run %s (ccsExecutionMode=%s)",
            trigger_name,
            run["name"],
            mode,
        )
        return True

    # ── State store ────────────────────────────────────────────

    def _load_state(self):
        raw = self._sensor_service.get_value(STATE_KEY)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                self._logger.warning("Corrupt state, resetting")
        return {}

    def _save_state(self, state):
        self._sensor_service.set_value(STATE_KEY, json.dumps(state))
