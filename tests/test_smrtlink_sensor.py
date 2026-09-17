"""Tests for sensors/smrtlink_sensor.py -- the trigger source for the whole
PacBio pipeline, and until now the only sensor here with no test at all.

What is actually under test is the *routing decision*: which of the two
triggers a completed run fires, and -- just as important -- when the sensor
refuses to fire one. A half-populated payload would register a run with
missing sample identity and mark it triggered, so it would never be revisited;
several of the tests below exist only to pin that down.

Why not tests/mock_smrtlink_server.py and tests/fixtures/collections_*.json:
those predate the current demux signal and are superseded, not used here.
Four of them are keyed on `ccsExecutionMode`, which was the original and wrong
signal (replaced by numBarcodes/multiJobId/numChildren), none carries
`numBarcodes`, and `runs.json` hard-codes `createdAt: 2026-06-01` -- inside a
*relative* 30-day window, so a fixture-driven test would have quietly stopped
exercising anything the moment that date aged out. Response bodies are built
here instead, following tests/test_resolve_longplex_pools.py's fake-client
precedent, and every date is computed relative to now.

Runs with or without the st2 source tree, and needs no network and no Flask:

    python3 -m unittest tests.test_smrtlink_sensor -v
"""

import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUN_UUID = "aaaaaaaa-0000-0000-0000-000000000001"
COLL_UUID = "bbbbbbbb-0000-0000-0000-000000000001"
COLL_UUID_2 = "bbbbbbbb-0000-0000-0000-000000000002"
CCS_ID = "cccccccc-0000-0000-0000-000000000001"
CCS_ID_2 = "cccccccc-0000-0000-0000-000000000002"


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO_ROOT, relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sensor_mod = _load("smrtlink_sensor_under_test", "sensors/smrtlink_sensor.py")


def recent_iso(days_ago=1):
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat().replace("+00:00", "Z")


def make_run(created_days_ago=1, name="test_run_001", uid=RUN_UUID):
    return {
        "name": name,
        "uniqueId": uid,
        "createdAt": recent_iso(created_days_ago),
        "summary": "Test run",
    }


def make_collection(
    uid=COLL_UUID,
    ccs_id=CCS_ID,
    status="Complete",
    num_barcodes=0,
    well="A01",
):
    return {
        "name": "test_run_001_{}".format(well),
        "uniqueId": uid,
        "status": status,
        "well": well,
        "instrumentName": "Revio",
        "collectionPathUri": "/data/revio/test_run_001/1_{}".format(well),
        "ccsId": ccs_id,
        "movieMinutes": 120,
        "numBarcodes": num_barcodes,
    }


class FakeLogger(object):
    """Records rather than discards: the no-demux warning is a documented
    part of the sensor's behaviour, so one test asserts it was emitted."""

    def __init__(self):
        self.messages = {"info": [], "warning": [], "error": []}

    def _record(self, level, msg, *args):
        try:
            rendered = msg % args if args else str(msg)
        except TypeError:
            rendered = "{} {}".format(msg, args)
        self.messages[level].append(rendered)

    def info(self, msg, *args, **kwargs):
        self._record("info", msg, *args)

    def warning(self, msg, *args, **kwargs):
        self._record("warning", msg, *args)

    def error(self, msg, *args, **kwargs):
        self._record("error", msg, *args)

    def text(self, level):
        return " | ".join(self.messages[level])


class FakeSensorService(object):
    def __init__(self, state=None):
        self.logger = FakeLogger()
        self.store = {}
        if state is not None:
            self.store[sensor_mod.STATE_KEY] = json.dumps(state)
        self.dispatched = []

    def get_logger(self, name):
        return self.logger

    def get_value(self, key, local=True, decrypt=False):
        return self.store.get(key)

    def set_value(self, key, value):
        self.store[key] = value

    def dispatch(self, trigger, payload):
        self.dispatched.append({"trigger": trigger, "payload": payload})

    # -- readers used by the assertions --

    def saved_state(self):
        raw = self.store.get(sensor_mod.STATE_KEY)
        return json.loads(raw) if raw else {}


class Boom(Exception):
    pass


class FakeSMRTClient(object):
    """Routes on the endpoint string, the way the real client is called.

    A fake rather than a mock because what is under test is a *sequence* of
    lookups -- collections, then barcodes per collection, then run details,
    then the ccsreads dataset -- and which of them get made at all.
    """

    def __init__(
        self,
        runs=None,
        collections=None,
        run_details=None,
        barcodes=None,
        datasets=None,
        fail_on=None,
    ):
        self.runs = runs if runs is not None else []
        self.collections = collections if collections is not None else {}
        self.run_details = run_details or {}
        self.barcodes = barcodes or {}
        self.datasets = datasets or {}
        self.fail_on = fail_on or ()
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)

        for fragment in self.fail_on:
            if fragment in endpoint:
                raise Boom("SMRT Link unreachable for {}".format(endpoint))

        if endpoint == "/smrt-link/runs":
            return self.runs

        if endpoint.endswith("/collections"):
            run_uuid = endpoint.split("/")[3]
            return self.collections.get(run_uuid, [])

        if endpoint.endswith("/barcodes"):
            coll_uuid = endpoint.split("/")[5]
            return self.barcodes.get(coll_uuid, [])

        if "/datasets/ccsreads/" in endpoint:
            return self.datasets.get(endpoint.rsplit("/", 1)[1], {})

        if endpoint.startswith("/smrt-link/runs/"):
            return self.run_details

        raise AssertionError("unexpected endpoint: {}".format(endpoint))

    def called(self, fragment):
        return any(fragment in call for call in self.calls)


def build_sensor(client, state=None, config=None):
    """A sensor with its client injected, skipping setup().

    setup() builds a real SMRTClient from config and datastore credentials;
    none of that is what these tests are about.
    """
    service = FakeSensorService(state=state)
    sensor = sensor_mod.SMRTLinkSensor(
        sensor_service=service, config=config or {"base_url": "https://smrtlink.test"}
    )
    sensor._client = client
    return sensor, service


class RoutingTests(unittest.TestCase):
    """Which trigger a completed run fires."""

    def _poll(self, collections, run_details=None, datasets=None, barcodes=None):
        client = FakeSMRTClient(
            runs=[make_run()],
            collections={RUN_UUID: collections},
            run_details=run_details if run_details is not None else {},
            datasets=datasets,
            barcodes=barcodes,
        )
        sensor, service = build_sensor(client)
        sensor.poll()
        return client, service

    def test_plain_run_fires_run_complete(self):
        _, service = self._poll([make_collection(num_barcodes=0)])

        self.assertEqual(len(service.dispatched), 1)
        self.assertEqual(
            service.dispatched[0]["trigger"], "ductus.pacbio_run_complete"
        )

    def test_native_barcodes_not_yet_split_fire_native_demux(self):
        _, service = self._poll(
            [make_collection(num_barcodes=12)],
            datasets={CCS_ID: {"numChildren": 0}},
        )

        self.assertEqual(
            service.dispatched[0]["trigger"],
            "ductus.pacbio_run_complete_native_demux",
        )

    def test_already_split_barcodes_do_not_fire_demux(self):
        """numChildren > 0 means lima has already run; demuxing again would
        duplicate work."""
        _, service = self._poll(
            [make_collection(num_barcodes=12)],
            datasets={CCS_ID: {"numChildren": 4}},
        )

        self.assertEqual(
            service.dispatched[0]["trigger"], "ductus.pacbio_run_complete"
        )

    def test_multijobid_wins_over_barcode_count(self):
        """SMRT Link has its own auto-analysis configured, so our demux would
        be redundant or would race it -- even with barcodes unsplit."""
        _, service = self._poll(
            [make_collection(num_barcodes=12)],
            run_details={"multiJobId": 77},
            datasets={CCS_ID: {"numChildren": 0}},
        )

        self.assertEqual(
            service.dispatched[0]["trigger"], "ductus.pacbio_run_complete"
        )

    def test_multijobid_skips_the_dataset_lookup_entirely(self):
        client, _ = self._poll(
            [make_collection(num_barcodes=12)],
            run_details={"multiJobId": 77},
            datasets={CCS_ID: {"numChildren": 0}},
        )

        self.assertFalse(client.called("/datasets/ccsreads/"))

    def test_single_barcode_short_circuits_before_the_dataset_lookup(self):
        """numBarcodes <= 1 returns before touching ccsId -- asserted because
        the short circuit is what keeps a plain run from paying for a lookup
        per collection."""
        client, _ = self._poll([make_collection(num_barcodes=1)])

        self.assertFalse(client.called("/datasets/ccsreads/"))

    def test_one_demuxing_collection_routes_the_whole_run(self):
        """The decision is any(), not all(): a mixed run must not be routed as
        no-demux just because most of its collections are plain."""
        _, service = self._poll(
            [
                make_collection(num_barcodes=0, well="A01"),
                make_collection(
                    uid=COLL_UUID_2, ccs_id=CCS_ID_2, num_barcodes=8, well="B01"
                ),
            ],
            datasets={CCS_ID_2: {"numChildren": 0}},
        )

        self.assertEqual(
            service.dispatched[0]["trigger"],
            "ductus.pacbio_run_complete_native_demux",
        )

    def test_longplex_lands_on_the_no_demux_path_and_says_so(self):
        """The documented blind spot, pinned so it cannot change silently.

        A LongPlex pool reports numBarcodes 0 or 1 -- seqWell barcodes never
        reach SMRT Link -- so it is indistinguishable here from a plain run.
        The warning is the only signal that a run may have been misrouted.
        """
        _, service = self._poll([make_collection(num_barcodes=1)])

        self.assertEqual(
            service.dispatched[0]["trigger"], "ductus.pacbio_run_complete"
        )
        self.assertIn("LongPlex", service.logger.text("warning"))


class CompletenessTests(unittest.TestCase):
    """When the sensor must not fire at all."""

    def test_incomplete_collection_blocks_the_run(self):
        client = FakeSMRTClient(
            runs=[make_run()],
            collections={
                RUN_UUID: [
                    make_collection(status="Complete", well="A01"),
                    make_collection(
                        uid=COLL_UUID_2, status="Transferring", well="B01"
                    ),
                ]
            },
        )
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.saved_state()[RUN_UUID]["triggered"])

    def test_a_run_with_no_collections_yet_does_not_fire(self):
        client = FakeSMRTClient(runs=[make_run()], collections={RUN_UUID: []})
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.saved_state()[RUN_UUID]["triggered"])

    def test_unanswered_collections_query_does_not_mark_triggered(self):
        """None means 'could not ask', which must read differently from an
        empty list -- both skip, but neither may conclude anything."""
        client = FakeSMRTClient(runs=[make_run()], fail_on=("/collections",))
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.saved_state()[RUN_UUID]["triggered"])

    def test_runs_older_than_the_cutoff_are_skipped(self):
        client = FakeSMRTClient(
            runs=[make_run(created_days_ago=sensor_mod.RUN_MAX_AGE_DAYS + 5)],
            collections={RUN_UUID: [make_collection()]},
        )
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(service.dispatched, [])
        self.assertNotIn(RUN_UUID, service.saved_state())

    def test_a_run_inside_the_cutoff_is_not_skipped(self):
        """Guards the boundary from the other side, so a broken date parse
        cannot make the suite pass by skipping everything."""
        client = FakeSMRTClient(
            runs=[make_run(created_days_ago=sensor_mod.RUN_MAX_AGE_DAYS - 5)],
            collections={RUN_UUID: [make_collection()]},
        )
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(len(service.dispatched), 1)

    def test_an_already_triggered_run_does_not_fire_twice(self):
        client = FakeSMRTClient(
            runs=[make_run()], collections={RUN_UUID: [make_collection()]}
        )
        sensor, service = build_sensor(
            client,
            state={
                RUN_UUID: {
                    "name": "test_run_001",
                    "triggered": True,
                    "first_seen": recent_iso(2),
                }
            },
        )
        sensor.poll()

        self.assertEqual(service.dispatched, [])


class PartialDataTests(unittest.TestCase):
    """The case the sensor's own comments care most about: if any part of the
    picture cannot be fetched, do not dispatch and do not mark triggered, so
    the next poll retries."""

    def _failing(self, fragment):
        client = FakeSMRTClient(
            runs=[make_run()],
            collections={RUN_UUID: [make_collection(num_barcodes=12)]},
            datasets={CCS_ID: {"numChildren": 0}},
            fail_on=(fragment,),
        )
        sensor, service = build_sensor(client)
        sensor.poll()
        return service

    def test_barcode_fetch_failure_aborts_without_dispatching(self):
        service = self._failing("/barcodes")

        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.saved_state()[RUN_UUID]["triggered"])

    def test_dataset_fetch_failure_aborts_without_dispatching(self):
        service = self._failing("/datasets/ccsreads/")

        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.saved_state()[RUN_UUID]["triggered"])

    def test_the_abort_is_logged_as_an_error(self):
        service = self._failing("/barcodes")

        self.assertIn("not dispatching", service.logger.text("error"))


class PayloadTests(unittest.TestCase):
    def setUp(self):
        client = FakeSMRTClient(
            runs=[make_run()],
            collections={RUN_UUID: [make_collection(num_barcodes=0)]},
            barcodes={
                COLL_UUID: [
                    {
                        "barcodeName": "bc1001",
                        "bioSampleName": "sample-a",
                        "sampleData": {"experiment_id": "EXP-123"},
                    },
                    {
                        "barcodeName": "bc1002",
                        "bioSampleName": "sample-b",
                        "sampleData": {},
                    },
                ]
            },
        )
        self.sensor, self.service = build_sensor(client)
        self.sensor.poll()
        self.payload = self.service.dispatched[0]["payload"]

    def test_carries_run_identity(self):
        self.assertEqual(self.payload["run_uuid"], RUN_UUID)
        self.assertEqual(self.payload["run_name"], "test_run_001")

    def test_collection_paths_come_from_collectionpathuri(self):
        self.assertEqual(
            self.payload["collection_paths"], ["/data/revio/test_run_001/1_A01"]
        )

    def test_experiment_id_is_lifted_out_of_sampledata(self):
        """The Sample-Setup convention the Processing API contract depends on:
        without this there is no way to know which Analysis row a well belongs
        to."""
        samples = self.payload["collections"][0]["barcoded_samples"]

        self.assertEqual(samples[0]["experiment_id"], "EXP-123")

    def test_a_barcode_without_sampledata_gets_a_null_experiment_id(self):
        """Explicitly None rather than absent, so a consumer can tell "not
        declared" from "key missing"."""
        samples = self.payload["collections"][0]["barcoded_samples"]

        self.assertIsNone(samples[1]["experiment_id"])

    def test_original_barcode_fields_are_preserved(self):
        samples = self.payload["collections"][0]["barcoded_samples"]

        self.assertEqual(samples[0]["barcodeName"], "bc1001")

    def test_a_collection_with_no_barcodes_is_logged_not_failed(self):
        """Legitimately empty for LongPlex pools, and possibly for plain
        single-sample wells -- whether Sample Setup creates a barcode record
        for a non-multiplexed well is still unconfirmed against a real server.
        """
        client = FakeSMRTClient(
            runs=[make_run()],
            collections={RUN_UUID: [make_collection()]},
            barcodes={COLL_UUID: []},
        )
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(len(service.dispatched), 1)
        self.assertEqual(
            service.dispatched[0]["payload"]["collections"][0]["barcoded_samples"],
            [],
        )
        self.assertIn("declared no barcodes", service.logger.text("info"))


class StateTests(unittest.TestCase):
    def test_a_dispatched_run_is_marked_triggered(self):
        client = FakeSMRTClient(
            runs=[make_run()], collections={RUN_UUID: [make_collection()]}
        )
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertTrue(service.saved_state()[RUN_UUID]["triggered"])

    def test_dispatch_return_value_is_not_consulted(self):
        """Pins what the code actually does, and why it must stay that way.

        pacbio_implementation_summary.md §2 says the run is marked triggered
        "only if the dispatch returned True". That reads as a guard on
        sensor_service.dispatch(), and it is not one: _dispatch_trigger
        returns True unconditionally once it has a complete payload, and
        never looks at what dispatch() returned.

        That cannot be repaired by propagating the return value.
        sensor_service.dispatch() returns None on success *and* None on a
        validation failure that dropped the trigger -- the chain bottoms out
        in TriggerDispatcher.dispatch(), which ends in publish_trigger() with
        no return statement (st2common/transport/reactor.py). Propagating it
        would make _dispatch_trigger always falsy, so no run would ever be
        marked triggered and every run inside the 30-day window would
        re-dispatch on every 600s poll.

        The real gap, which a return-value check cannot close: if payload
        validation fails while system.validate_trigger_payload is enabled,
        st2 logs a warning, drops the trigger and returns None --
        indistinguishable from success. The run is marked triggered and never
        retried. Closing that means validating the payload before dispatching,
        not inspecting a return value that carries no status.
        """

        class RefusingService(FakeSensorService):
            def dispatch(self, trigger, payload):
                super().dispatch(trigger, payload)
                return False

        client = FakeSMRTClient(
            runs=[make_run()], collections={RUN_UUID: [make_collection()]}
        )
        service = RefusingService()
        sensor = sensor_mod.SMRTLinkSensor(
            sensor_service=service, config={"base_url": "https://smrtlink.test"}
        )
        sensor._client = client
        sensor.poll()

        self.assertTrue(service.saved_state()[RUN_UUID]["triggered"])

    def test_corrupt_state_resets_and_replays_the_window(self):
        """docs/deferred_findings.md item 8: a corrupt KV entry reads as "no
        run has ever been triggered", so every run inside the 30-day window is
        dispatched again. Pinned so the fix, when it comes, has a failing test
        to flip."""
        client = FakeSMRTClient(
            runs=[make_run()], collections={RUN_UUID: [make_collection()]}
        )
        service = FakeSensorService()
        service.store[sensor_mod.STATE_KEY] = "{not json"
        sensor = sensor_mod.SMRTLinkSensor(
            sensor_service=service, config={"base_url": "https://smrtlink.test"}
        )
        sensor._client = client
        sensor.poll()

        self.assertEqual(len(service.dispatched), 1)
        self.assertIn("Corrupt state", service.logger.text("warning"))

    def test_unreachable_smrtlink_leaves_state_untouched(self):
        client = FakeSMRTClient(fail_on=("/smrt-link/runs",))
        sensor, service = build_sensor(client)
        sensor.poll()

        self.assertEqual(service.dispatched, [])
        self.assertEqual(service.saved_state(), {})


if __name__ == "__main__":
    unittest.main()
