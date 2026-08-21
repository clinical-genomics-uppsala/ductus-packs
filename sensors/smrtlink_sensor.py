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

    def _get_run_details(self, run_uuid):
        try:
            return self._client.get(f"/smrt-link/runs/{run_uuid}")
        except Exception as e:
            self._logger.error(f"Failed to fetch run details for {run_uuid}: {e}")
            return None

    def _dataset_already_split(self, ccs_id):
        """True if a collection's ConsensusReadSet already has child datasets,
        i.e. it has already been demultiplexed."""
        try:
            dataset = self._client.get(f"/smrt-link/datasets/ccsreads/{ccs_id}")
        except Exception as e:
            self._logger.error(f"Could not fetch dataset {ccs_id}: {e}")
            return False
        return dataset.get("numChildren", 0) > 0

    def _get_barcoded_samples(self, run_uuid, collection_uuid):
        """Per-barcode sample identity for a collection, plus the LIMS
        experiment_id stashed in each barcode's sampleData (Sample-Setup
        convention -- see docs/pacbio_processing_api_contract.md).

        Empty for a collection with no declared barcodes. That includes
        LongPlex pools (seqWell barcodes are invisible to SMRT Link) and
        possibly plain single-sample wells too -- whether Sample Setup
        creates a barcode record for a non-multiplexed well is unconfirmed.
        """
        try:
            barcodes = self._client.get(
                f"/smrt-link/runs/{run_uuid}/collections/{collection_uuid}/barcodes"
            )
        except Exception as e:
            self._logger.error(f"Could not fetch barcodes for collection {collection_uuid}: {e}")
            return []

        samples = []
        for entry in barcodes or []:
            sample_data = entry.get("sampleData") or {}
            samples.append({
                **entry,
                "experiment_id": sample_data.get("experiment_id"),
            })
        return samples

    def _collection_needs_demux(self, collection):
        """Detects SMRT-Link-visible (native PacBio barcode) multiplexing that
        hasn't already been split into per-sample datasets.

        Does NOT detect LongPlex pools: seqWell barcodes are internal to the
        library and invisible to SMRT Link, so a LongPlex well typically
        reports numBarcodes 0 or 1 despite containing many pooled samples.
        LongPlex detection needs a separate, out-of-band signal (naming
        convention, a populated `sampleData` field, or an external sample
        sheet) that isn't wired up yet — see
        docs/pacbio_processing_api_contract.md.
        """
        if collection.get("numBarcodes", 0) <= 1:
            return False
        ccs_id = collection.get("ccsId")
        if ccs_id and self._dataset_already_split(ccs_id):
            return False
        return True

    # ── Trigger ────────────────────────────────────────────────

    def _dispatch_trigger(self, run):
        try:
            collections = self._client.get(f"/smrt-link/runs/{run['uniqueId']}/collections")
        except Exception as e:
            self._logger.error(f"Could not fetch collections for trigger payload: {e}")
            collections = []

        for collection in collections:
            collection["barcoded_samples"] = self._get_barcoded_samples(
                run["uniqueId"], collection.get("uniqueId")
            )

        run_details = self._get_run_details(run["uniqueId"]) or {}
        multi_job_id = run_details.get("multiJobId")

        if multi_job_id:
            # SMRT Link already has an analysis multi-job configured to fire
            # automatically once collections import -- our own demux step
            # would be redundant (or worse, race with it).
            self._logger.info(
                "Run %s has multiJobId=%s; SMRT Link will handle analysis/demux automatically",
                run["name"],
                multi_job_id,
            )
            needs_demux = False
        else:
            needs_demux = any(self._collection_needs_demux(c) for c in collections)

        if not needs_demux:
            self._logger.warning(
                "Run %s routed as no-demux-needed based on numBarcodes/multiJobId/numChildren -- "
                "this cannot detect LongPlex pools (seqWell barcodes are invisible to SMRT Link), "
                "so a LongPlex run could be silently misrouted here until an out-of-band LongPlex "
                "marker is implemented.",
                run["name"],
            )

        trigger_name = (
            "ductus.pacbio_run_complete_longplex"
            if needs_demux
            else "ductus.pacbio_run_complete"
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
            "Dispatched %s for run %s (needs_demux=%s)",
            trigger_name,
            run["name"],
            needs_demux,
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
