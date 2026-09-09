"""Tests for actions/resolve_longplex_pools.py and the SMRTClient additions.

The SMRT Link responses here are hand-built from the fields the swagger spec
declares on ConsensusReadDetails (parentUuid, dnaBarcodeName, uuid, path,
numChildren, wellName) rather than captured from a live server -- there isn't
one reachable from a dev machine. Where that leaves an assumption rather than
a fact, the assumption is named in the test, so a real capture can later
confirm or contradict it in one place.

Runs with or without the st2 source tree: st2common is stubbed only when it
is genuinely absent, so under st2-run-pack-tests the real Action base class
is exercised.

    python3 -m unittest tests.test_resolve_longplex_pools -v
"""

import importlib.util
import os
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:  # pragma: no cover - depends on the environment, not the code
    import st2common.runners.base_action  # noqa: F401
except ImportError:
    # Minimal stand-in for the one thing the action uses from st2: a base
    # class holding config/action_service/logger. Installed only when st2 is
    # not on the path, so this never shadows the real one.
    _base = types.ModuleType("st2common.runners.base_action")

    class _Action(object):
        def __init__(self, config=None, action_service=None):
            self.config = config or {}
            self.action_service = action_service
            self.logger = _NullLogger()

    _base.Action = _Action
    _runners = types.ModuleType("st2common.runners")
    _runners.base_action = _base
    _st2common = types.ModuleType("st2common")
    _st2common.runners = _runners
    sys.modules.setdefault("st2common", _st2common)
    sys.modules.setdefault("st2common.runners", _runners)
    sys.modules.setdefault("st2common.runners.base_action", _base)


class _NullLogger(object):
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO_ROOT, relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rlp = _load("resolve_longplex_pools", "actions/resolve_longplex_pools.py")
smrt = _load("smrt_client_under_test", "lib/smrt_client.py")


class FakeSMRTClient(object):
    """Stands in for SMRTClient, recording what was asked for.

    A fake rather than a mock because the action makes a *sequence* of calls
    whose shape is the thing under test: collections, then children per
    collection, then dataset details per child.
    """

    def __init__(self, run=None, collections=None, children=None, details=None):
        self.run = run or {"name": "test_run_001"}
        self.collections = collections or []
        self.children = children or {}
        self.details = details or {}
        self.detail_calls = []

    def get_run(self, run_uuid):
        return self.run

    def get_run_collections(self, run_uuid):
        return self.collections

    def get_child_ccsreads(self, parent_uuid):
        return self.children.get(parent_uuid, [])

    def get_dataset_bam_paths(self, dataset_id, dataset_type="ccsreads"):
        self.detail_calls.append(dataset_id)
        return self.details.get(dataset_id, [])


def child(barcode, uuid):
    return {
        "uuid": uuid,
        "dnaBarcodeName": barcode,
        "parentUuid": "cccccccc-0000-0000-0000-000000000001",
        "numChildren": 0,
    }


def collection(name="test_run_001_A01", ccs_id="cccccccc-0000-0000-0000-000000000001",
               well="A01"):
    return {
        "name": name,
        "uniqueId": "bbbbbbbb-0000-0000-0000-000000000001",
        "status": "Complete",
        "well": well,
        "ccsId": ccs_id,
        "collectionPathUri": "/data/revio/test_run_001/1_A01",
    }


def make_action(client, config=None):
    action = rlp.ResolveLongplexPools()
    action.config = config or {"longplex_default_barcode_set": "set1"}
    action.logger = _NullLogger()
    action._client = lambda: client
    return action


class TestPoolIdFromBarcode(unittest.TestCase):
    """dnaBarcodeName comes back as an i7--i5 pair; pool_ID forbids '-'."""

    def setUp(self):
        self.action = make_action(FakeSMRTClient())

    def test_symmetric_pair_collapses_to_one_half(self):
        self.assertEqual(
            self.action._pool_id_from_barcode("bc1015--bc1015"), "bc1015"
        )

    def test_unpaired_name_is_taken_as_is(self):
        self.assertEqual(self.action._pool_id_from_barcode("bc1015"), "bc1015")

    def test_asymmetric_pair_is_rejected_rather_than_guessed(self):
        """Neither half nor concatenation is a name that appears anywhere."""
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            self.action._pool_id_from_barcode("bc1015--bc1016")
        self.assertIn("asymmetric", str(caught.exception))

    def test_empty_barcode_is_rejected(self):
        with self.assertRaises(rlp.ResolveLongplexPoolsError):
            self.action._pool_id_from_barcode("")

    def test_non_alphanumeric_barcode_is_rejected_and_named(self):
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            self.action._pool_id_from_barcode("bc_1015")
        self.assertIn("'bc_1015'", str(caught.exception))


class TestPoolResolution(unittest.TestCase):
    def test_two_pools_on_one_cell(self):
        ccs = "cccccccc-0000-0000-0000-000000000001"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [
                child("bc1015--bc1015", "dddddddd-15"),
                child("bc1016--bc1016", "dddddddd-16"),
            ]},
            details={
                "dddddddd-15": ["/data/pools/m84.hifi_reads.bc1015.bam"],
                "dddddddd-16": ["/data/pools/m84.hifi_reads.bc1016.bam"],
            },
        )
        _, output = make_action(client).run(
            run_id="run-1", dest_dir="/scratch/run_x"
        )
        pools = output["manifest"]["pools"]

        self.assertEqual([p["pool_ID"] for p in pools], ["bc1015", "bc1016"])
        # pool_path is the compute-side path -- this action runs after the
        # transfer step, so the SMRT Link path is only the basename source.
        self.assertEqual(
            pools[0]["pool_path"],
            "/scratch/run_x/bc1015/m84.hifi_reads.bc1015.bam",
        )

    def test_undemultiplexed_collection_is_one_pool_named_after_its_well(self):
        """No outer barcode declared is a valid single-pool case."""
        ccs = "cccccccc-0000-0000-0000-000000000001"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs, well="A01")],
            children={},
            details={ccs: ["/data/m84.hifi_reads.bam"]},
        )
        _, output = make_action(client).run(run_id="run-1", dest_dir="/scratch/run_y")
        manifest = output["manifest"]

        self.assertEqual([p["pool_ID"] for p in manifest["pools"]], ["A01"])
        self.assertTrue(
            any("no outer barcode" in w for w in manifest["warnings"]),
            manifest["warnings"],
        )

    def test_pool_id_collision_across_cells_is_rejected(self):
        """pool_ID prefixes every output path, so it must be unique per run."""
        client = FakeSMRTClient(
            collections=[
                collection(name="cell1", ccs_id="ccs-1", well="A01"),
                collection(name="cell2", ccs_id="ccs-2", well="A01"),
            ],
            children={},
            details={"ccs-1": ["/data/a.bam"], "ccs-2": ["/data/b.bam"]},
        )
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            make_action(client).run(run_id="run-1", dest_dir="/scratch/x")
        self.assertIn("unique across the run", str(caught.exception))

    def test_a_run_with_no_collections_is_rejected(self):
        with self.assertRaises(rlp.ResolveLongplexPoolsError):
            make_action(FakeSMRTClient(collections=[])).run(
                run_id="run-1", dest_dir="/scratch/x"
            )

    def test_collection_without_a_ccs_id_is_skipped_with_a_warning(self):
        no_ccs = collection(name="aborted", ccs_id=None)
        no_ccs.pop("ccsId")
        good = collection(name="good", ccs_id="ccs-2", well="B01")
        client = FakeSMRTClient(
            collections=[no_ccs, good],
            children={},
            details={"ccs-2": ["/data/b.bam"]},
        )
        _, output = make_action(client).run(run_id="run-1", dest_dir="/scratch/x")
        self.assertEqual([p["pool_ID"] for p in output["manifest"]["pools"]], ["B01"])
        self.assertTrue(
            any("no ccsId" in w for w in output["manifest"]["warnings"])
        )

    def test_manifest_pools_are_sorted(self):
        """Byte-identical manifests for identical runs, like the CSVs."""
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [
                child("bc1016--bc1016", "d-16"),
                child("bc1015--bc1015", "d-15"),
            ]},
            details={"d-15": ["/data/a.bam"], "d-16": ["/data/b.bam"]},
        )
        _, output = make_action(client).run(run_id="run-1", dest_dir="/scratch/x")
        self.assertEqual(
            [p["pool_ID"] for p in output["manifest"]["pools"]], ["bc1015", "bc1016"]
        )


class TestBamResolution(unittest.TestCase):
    def test_a_pool_with_no_bam_is_rejected(self):
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [child("bc1015--bc1015", "d-15")]},
            details={"d-15": []},
        )
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            make_action(client).run(run_id="run-1", dest_dir="/scratch/x")
        self.assertIn("lists no .bam", str(caught.exception))

    def test_several_bams_is_rejected_rather_than_picking_one(self):
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [child("bc1015--bc1015", "d-15")]},
            details={"d-15": ["/data/a.bam", "/data/b.bam"]},
        )
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            make_action(client).run(run_id="run-1", dest_dir="/scratch/x")
        self.assertIn("not ours to guess", str(caught.exception))


class TestOverrides(unittest.TestCase):
    def test_override_wins_and_skips_the_dataset_lookup(self):
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [child("bc1015--bc1015", "d-15")]},
            details={"d-15": ["/data/derived.bam"]},
        )
        _, output = make_action(client).run(
            run_id="run-1",
            dest_dir="/scratch/x",
            bam_path_overrides={"bc1015": "/staged/elsewhere.bam"},
        )
        self.assertEqual(
            output["manifest"]["pools"][0]["pool_path"], "/staged/elsewhere.bam"
        )
        self.assertEqual(client.detail_calls, [])

    def test_override_for_a_pool_the_run_lacks_is_an_error(self):
        """Otherwise the pool they meant to redirect stays silently derived."""
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={ccs: [child("bc1015--bc1015", "d-15")]},
            details={"d-15": ["/data/a.bam"]},
        )
        with self.assertRaises(rlp.ResolveLongplexPoolsError) as caught:
            make_action(client).run(
                run_id="run-1",
                dest_dir="/scratch/x",
                bam_path_overrides={"bc9999": "/staged/x.bam"},
            )
        self.assertIn("bc9999", str(caught.exception))


class TestManifestShape(unittest.TestCase):
    def test_barcode_set_falls_back_to_the_pack_config(self):
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={},
            details={ccs: ["/data/a.bam"]},
        )
        action = make_action(client, config={"longplex_default_barcode_set": "set3"})
        _, output = action.run(run_id="run-1", dest_dir="/scratch/x")
        self.assertEqual(output["manifest"]["barcode_set"], "set3")

    def test_explicit_barcode_set_wins(self):
        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={},
            details={ccs: ["/data/a.bam"]},
        )
        _, output = make_action(client).run(
            run_id="run-1", dest_dir="/scratch/x", barcode_set="set2"
        )
        self.assertEqual(output["manifest"]["barcode_set"], "set2")

    def test_manifest_is_written_when_a_path_is_given(self):
        import json
        import tempfile

        ccs = "ccs-1"
        client = FakeSMRTClient(
            collections=[collection(ccs_id=ccs)],
            children={},
            details={ccs: ["/data/a.bam"]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "manifest.json")
            make_action(client).run(
                run_id="run-1", dest_dir="/scratch/x", manifest_path=path
            )
            with open(path, encoding="utf-8") as handle:
                written = json.load(handle)
        self.assertEqual(written["run_id"], "run-1")
        self.assertEqual(written["dest_dir"], "/scratch/x")


class TestBamResourceExtraction(unittest.TestCase):
    """lib/smrt_client._collect_bam_resource_ids.

    ASSUMPTION under test: the /details response is the DataSet XML rendered
    as JSON, whose exact nesting and capitalisation we have not seen. The
    walker is therefore shape-tolerant, and these cases pin the behaviour
    that matters -- BAMs found wherever they sit, .pbi never mistaken for
    one -- rather than a specific layout.
    """

    def test_finds_a_bam_nested_anywhere(self):
        details = {
            "DataSet": {
                "ExternalResources": {
                    "ExternalResource": [
                        {"ResourceId": "/data/m84.hifi_reads.bam"},
                    ]
                }
            }
        }
        self.assertEqual(
            smrt._collect_bam_resource_ids(details), ["/data/m84.hifi_reads.bam"]
        )

    def test_ignores_pbi_and_other_sidecars(self):
        details = {
            "ExternalResources": [
                {
                    "resourceId": "/data/m84.hifi_reads.bam",
                    "FileIndices": [{"resourceId": "/data/m84.hifi_reads.bam.pbi"}],
                },
                {"resourceId": "/data/m84.consensusreadset.xml"},
            ]
        }
        self.assertEqual(
            smrt._collect_bam_resource_ids(details), ["/data/m84.hifi_reads.bam"]
        )

    def test_a_bam_named_in_prose_is_not_treated_as_a_resource(self):
        """Only resource-id-ish keys count, so a comment cannot smuggle a path."""
        details = {
            "description": "superseded by m84.hifi_reads.bam",
            "comments": "/data/old.bam",
        }
        self.assertEqual(smrt._collect_bam_resource_ids(details), [])

    def test_the_same_resource_listed_twice_is_returned_once(self):
        details = {
            "a": {"resourceId": "/data/x.bam"},
            "b": {"resourceId": "/data/x.bam"},
        }
        self.assertEqual(smrt._collect_bam_resource_ids(details), ["/data/x.bam"])

    def test_empty_when_there_is_nothing_to_find(self):
        self.assertEqual(smrt._collect_bam_resource_ids({}), [])
        self.assertEqual(smrt._collect_bam_resource_ids([]), [])


if __name__ == "__main__":
    unittest.main()
