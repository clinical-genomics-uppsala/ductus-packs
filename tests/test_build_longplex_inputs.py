"""Tests for scripts/build_longplex_inputs.py.

Plain unittest, not BaseSensorTestCase: the script under test is the half of
LongPlex input generation that has no network and no ST2 dependency, which is
exactly what makes it testable this way. It can be run without the st2 source
tree:

    python3 -m unittest tests.test_build_longplex_inputs -v

The manifests and sample maps come from tests/fixtures/. Real temp
directories and real (tiny) BAM files are used rather than a fake filesystem,
so the existence and zero-byte checks are actually exercised.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "build_longplex_inputs.py")

# Loaded by path: scripts/ is a deployment directory, not an importable
# package, and keeping it that way is what lets the file be copied to a
# cluster on its own.
_spec = importlib.util.spec_from_file_location("build_longplex_inputs", SCRIPT)
blx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blx)


def load_manifest(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def fixture(name):
    return os.path.join(FIXTURES, name)


class LongPlexInputsTestCase(unittest.TestCase):
    """Builds a throwaway run directory that looks like a completed transfer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="longplex-test-")
        self.dest_dir = os.path.join(self.tmp, "run")
        self.barcode_dir = os.path.join(self.tmp, "barcodes")
        os.makedirs(self.dest_dir)
        os.makedirs(self.barcode_dir)
        for index in (7, 5):
            for barcode_set in ("set1", "set2", "set3"):
                self._write(
                    os.path.join(
                        self.barcode_dir,
                        "LongPlex_%s_i%d_trimmed_adapters.fa" % (barcode_set, index),
                    ),
                    ">bc\nACGT\n",
                )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, content="x"):
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _stage(self, manifest_name, empty_pools=(), missing_pools=()):
        """Rewrite a fixture manifest onto the temp dir and create its BAMs.

        empty_pools get a zero-byte BAM, missing_pools get none at all --
        the two ways a transfer step fails.
        """
        manifest = load_manifest(manifest_name)
        manifest["dest_dir"] = self.dest_dir
        for pool in manifest["pools"]:
            pool["pool_path"] = os.path.join(
                self.dest_dir, pool["pool_ID"], pool["bam_basename"]
            )
            if pool["pool_ID"] in missing_pools:
                continue
            content = "" if pool["pool_ID"] in empty_pools else "BAM"
            self._write(pool["pool_path"], content)
        return manifest

    def _build(self, manifest, **kwargs):
        kwargs.setdefault("dest_dir", self.dest_dir)
        kwargs.setdefault("barcode_dir", self.barcode_dir)
        kwargs.setdefault("barcode_set", "set1")
        return blx.build_inputs(manifest=manifest, **kwargs)

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class TestExactCsvContent(LongPlexInputsTestCase):
    def test_multi_pool_writes_one_sheet_with_both_pools(self):
        """Two pools on one cell -> one pool_sheet, two rows, both prefixes."""
        manifest = self._stage("longplex_pool_manifest.json")
        result = self._build(
            manifest, sample_map=fixture("longplex_sample_map_pooled.csv")
        )

        expected_pool_sheet = (
            "pool_ID,pool_path,i7_barcode,i5_barcode\n"
            "bc1015,{dest}/bc1015/m84.hifi_reads.bc1015.bam,{i7},{i5}\n"
            "bc1016,{dest}/bc1016/m84.hifi_reads.bc1016.bam,{i7},{i5}\n"
        ).format(
            dest=self.dest_dir,
            i7=os.path.join(self.barcode_dir, "LongPlex_set1_i7_trimmed_adapters.fa"),
            i5=os.path.join(self.barcode_dir, "LongPlex_set1_i5_trimmed_adapters.fa"),
        )
        self.assertEqual(self._read(result["pool_sheet"]), expected_pool_sheet)

        # rename_map keys join pool and well with '.', never '_', and carry
        # entries for every pool -- the pipeline filters per pool itself.
        self.assertEqual(
            self._read(result["rename_map"]),
            "pool_ID.well_ID,sample_ID\n"
            "bc1015.A01,CU01\n"
            "bc1015.B01,CU02\n"
            "bc1016.A01,CU03\n",
        )
        self.assertEqual(
            [pool["num_wells"] for pool in result["pools"]], [2, 1]
        )

    def test_single_undemultiplexed_pool(self):
        """A collection with no outer barcode declared is a valid one-pool run."""
        manifest = self._stage("longplex_pool_manifest_single.json")
        result = self._build(manifest, sample_map=fixture("longplex_sample_map.csv"))

        self.assertEqual(
            self._read(result["pool_sheet"]).splitlines()[1].split(",")[0], "A01"
        )
        # No pool column in this sheet and exactly one pool, so every row
        # belongs to it without a join.
        self.assertEqual(
            self._read(result["rename_map"]),
            "pool_ID.well_ID,sample_ID\n"
            "A01.A01,CU01\n"
            "A01.B01,CU02\n"
            "A01.H12,CU03\n",
        )

    def test_no_sample_map_means_no_rename_map(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        result = self._build(manifest, sample_map=None)

        self.assertIsNone(result["rename_map"])
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "rename_map.csv")))
        self.assertTrue(os.path.exists(result["pool_sheet"]))
        # num_wells has no pre-demux source: the inner wells are invisible to
        # SMRT Link, so with no sheet there is nothing to count.
        self.assertEqual([pool["num_wells"] for pool in result["pools"]], [None])

    def test_barcode_set_selects_the_fasta_pair(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        result = self._build(manifest, barcode_set="set3")
        row = self._read(result["pool_sheet"]).splitlines()[1]
        self.assertIn("LongPlex_set3_i7_trimmed_adapters.fa", row)
        self.assertIn("LongPlex_set3_i5_trimmed_adapters.fa", row)


class TestDeterminismAndIdempotency(LongPlexInputsTestCase):
    def test_output_is_byte_identical_across_runs(self):
        """Same inputs, same bytes -- what makes `nextflow -resume` behave."""
        manifest = self._stage("longplex_pool_manifest.json")
        first = self._build(
            manifest, sample_map=fixture("longplex_sample_map_pooled.csv")
        )
        first_bytes = (self._read(first["pool_sheet"]), self._read(first["rename_map"]))

        # Re-run over the existing dest_dir: must overwrite cleanly.
        second = self._build(
            manifest, sample_map=fixture("longplex_sample_map_pooled.csv")
        )
        self.assertEqual(
            (self._read(second["pool_sheet"]), self._read(second["rename_map"])),
            first_bytes,
        )

    def test_pool_order_in_the_manifest_does_not_change_output(self):
        manifest = self._stage("longplex_pool_manifest.json")
        forward = self._read(self._build(manifest)["pool_sheet"])
        manifest["pools"].reverse()
        self.assertEqual(self._read(self._build(manifest)["pool_sheet"]), forward)


class TestTransferFailures(LongPlexInputsTestCase):
    def test_missing_bam_raises_and_writes_nothing(self):
        manifest = self._stage("longplex_pool_manifest.json", missing_pools=("bc1016",))
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("bc1016", str(caught.exception))
        self.assertIn("transfer step did not put it there", str(caught.exception))
        # Nothing written: a pool_sheet beside a rejected run is worse than none.
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "pool_sheet.csv")))

    def test_zero_byte_bam_raises(self):
        manifest = self._stage("longplex_pool_manifest.json", empty_pools=("bc1015",))
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("zero bytes", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "pool_sheet.csv")))

    def test_all_problems_are_reported_together(self):
        """One round trip per typo is miserable; collect them."""
        manifest = self._stage(
            "longplex_pool_manifest.json",
            missing_pools=("bc1015",),
            empty_pools=("bc1016",),
        )
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertEqual(len(caught.exception.errors), 2)


class TestValidationRules(LongPlexInputsTestCase):
    """One case per validation rule, each asserting the offending value is named."""

    def _sheet(self, rows, header="Project,Run_nr,Sample_ID,Index_ID"):
        path = os.path.join(self.tmp, "sheet.csv")
        self._write(path, header + "\n" + "\n".join(rows) + "\n")
        return path

    def _expect_error(self, manifest_name="longplex_pool_manifest_single.json", **kwargs):
        manifest = self._stage(manifest_name)
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, **kwargs)
        return str(caught.exception)

    def test_pool_id_with_underscore_is_rejected_and_named(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        manifest["pools"][0]["pool_ID"] = "run_x"
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("'run_x'", str(caught.exception))

    def test_empty_pool_id_is_rejected(self):
        """The pipeline's own regex uses `*`, so empty passes theirs."""
        manifest = self._stage("longplex_pool_manifest_single.json")
        manifest["pools"][0]["pool_ID"] = ""
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("may not be empty", str(caught.exception))

    def test_single_digit_well_is_rejected_not_padded(self):
        message = self._expect_error(sample_map=self._sheet(["p,R1,CU01,A1"]))
        self.assertIn("'A1'", message)
        self.assertIn("01-12", message)

    def test_well_column_zero_is_rejected(self):
        """The one real example sheet has an A00 row. A00 is not a well."""
        message = self._expect_error(sample_map=self._sheet(["p,R1,CU01,A00"]))
        self.assertIn("'A00'", message)

    def test_well_row_beyond_h_is_rejected(self):
        message = self._expect_error(sample_map=self._sheet(["p,R1,CU01,J01"]))
        self.assertIn("'J01'", message)

    def test_duplicate_sample_id_is_rejected(self):
        message = self._expect_error(
            sample_map=self._sheet(["p,R1,CU01,A01", "p,R1,CU01,B01"])
        )
        self.assertIn("'CU01'", message)
        self.assertIn("already used", message)

    def test_sample_id_with_a_path_separator_is_rejected(self):
        message = self._expect_error(sample_map=self._sheet(["p,R1,a/b,A01"]))
        self.assertIn("'a/b'", message)

    def test_underscore_in_sample_id_is_allowed(self):
        """Unlike pool_ID -- the vendor README gives bc1015_sample1 as valid."""
        manifest = self._stage("longplex_pool_manifest_single.json")
        result = self._build(manifest, sample_map=self._sheet(["p,R1,CU_01,A01"]))
        self.assertIn("CU_01", self._read(result["rename_map"]))

    def test_pool_not_in_the_run_is_rejected(self):
        """The pipeline drops an unmatched prefix's whole rename set silently."""
        manifest = self._stage("longplex_pool_manifest.json")
        sheet = self._sheet(
            ["p,R1,CU01,A01,bc9999"],
            header="Project,Run_nr,Sample_ID,Index_ID,pool_ID",
        )
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, sample_map=sheet)
        self.assertIn("'bc9999'", str(caught.exception))
        self.assertIn("silently drop", str(caught.exception))

    def test_multi_pool_without_a_pool_column_is_rejected(self):
        """The sheet cannot be joined on well: different namespaces."""
        manifest = self._stage("longplex_pool_manifest.json")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, sample_map=fixture("longplex_sample_map.csv"))
        message = str(caught.exception)
        self.assertIn("bc1015", message)
        self.assertIn("bc1016", message)
        self.assertIn("no pool column", message)

    def test_partially_filled_pool_column_is_rejected(self):
        manifest = self._stage("longplex_pool_manifest.json")
        sheet = self._sheet(
            ["p,R1,CU01,A01,bc1015", "p,R1,CU02,B01,"],
            header="Project,Run_nr,Sample_ID,Index_ID,pool_ID",
        )
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, sample_map=sheet)
        self.assertIn("leave it blank", str(caught.exception))

    def test_duplicate_pool_well_key_is_rejected(self):
        message = self._expect_error(
            sample_map=self._sheet(["p,R1,CU01,A01", "p,R1,CU02,A01"])
        )
        self.assertIn("A01.A01", message)

    def test_missing_barcode_fasta_is_named(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        os.remove(
            os.path.join(self.barcode_dir, "LongPlex_set1_i7_trimmed_adapters.fa")
        )
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("LongPlex_set1_i7_trimmed_adapters.fa", str(caught.exception))

    def test_unwritable_dest_dir_is_named(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        gone = os.path.join(self.tmp, "nope")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, dest_dir=gone)
        self.assertIn(gone, str(caught.exception))

    def test_unknown_barcode_set_is_named(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest, barcode_set="set9")
        self.assertIn("'set9'", str(caught.exception))

    def test_pool_path_not_a_bam_is_rejected(self):
        manifest = self._stage("longplex_pool_manifest_single.json")
        renamed = manifest["pools"][0]["pool_path"].replace(".bam", ".fastq")
        self._write(renamed, "x")
        manifest["pools"][0]["pool_path"] = renamed
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build(manifest)
        self.assertIn("does not end in .bam", str(caught.exception))

    def test_manifest_with_no_pools_is_rejected(self):
        with self.assertRaises(blx.LongPlexInputError) as caught:
            self._build({"run_id": "r", "pools": []})
        self.assertIn("no pools", str(caught.exception))


class TestSampleMapParsing(LongPlexInputsTestCase):
    def test_headers_are_matched_case_insensitively_with_whitespace(self):
        path = os.path.join(self.tmp, "odd.csv")
        self._write(path, " SAMPLE_ID , index id \nCU01,A01\n")
        records = blx.parse_sample_map(path)
        self.assertEqual(records[0]["well"], "A01")
        self.assertEqual(records[0]["sample_id"], "CU01")

    def test_crlf_and_bom_do_not_leak_into_values(self):
        path = os.path.join(self.tmp, "excel.csv")
        with open(path, "wb") as handle:
            handle.write("﻿Sample_ID,Index_ID\r\nCU01,A01\r\n".encode("utf-8"))
        records = blx.parse_sample_map(path)
        self.assertEqual(records[0]["sample_id"], "CU01")
        self.assertEqual(records[0]["well"], "A01")

    def test_preamble_rows_above_the_header_are_skipped(self):
        """A submission workbook carries instructions above its header row."""
        path = os.path.join(self.tmp, "preamble.csv")
        self._write(
            path,
            "Filled in by sender.\n"
            "If not DNA specify in comment.\n"
            "\n"
            "Sample_ID,Index_ID\n"
            "CU01,A01\n",
        )
        records = blx.parse_sample_map(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["row"], 5)

    def test_blank_rows_are_not_data(self):
        path = os.path.join(self.tmp, "gaps.csv")
        self._write(path, "Sample_ID,Index_ID\nCU01,A01\n,\nCU02,B01\n")
        self.assertEqual(len(blx.parse_sample_map(path)), 2)

    def test_row_numbers_are_the_sheet_row(self):
        """Error messages are only actionable if they point at the right row."""
        path = os.path.join(self.tmp, "rows.csv")
        self._write(path, "Sample_ID,Index_ID\nCU01,A01\nCU02,ZZZ\n")
        records = blx.parse_sample_map(path)
        self.assertEqual([r["row"] for r in records], [2, 3])

    def test_two_columns_claiming_the_same_role_is_rejected(self):
        """First-wins would emit a valid-looking sheet from the wrong column."""
        path = os.path.join(self.tmp, "ambiguous.csv")
        self._write(path, "Sample,Sample_ID,Index_ID\nwrong,CU01,A01\n")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            blx.parse_sample_map(path)
        message = str(caught.exception)
        self.assertIn("sample", message)
        self.assertIn("nothing to guess at", message)

    def test_a_bare_barcode_column_is_not_taken_as_the_pool(self):
        """It could mean either barcode layer on a PacBio sheet."""
        path = os.path.join(self.tmp, "barcode.csv")
        self._write(path, "Sample_ID,Index_ID,Barcode\nCU01,A01,bc1015\n")
        self.assertIsNone(blx.parse_sample_map(path)[0]["pool"])

    def test_pool_barcode_is_accepted_as_the_pool(self):
        path = os.path.join(self.tmp, "pool_barcode.csv")
        self._write(path, "Sample_ID,Index_ID,pool_barcode\nCU01,A01,bc1015\n")
        self.assertEqual(blx.parse_sample_map(path)[0]["pool"], "bc1015")

    def test_a_sheet_with_no_recognisable_header_is_rejected(self):
        path = os.path.join(self.tmp, "junk.csv")
        self._write(path, "a,b,c\n1,2,3\n")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            blx.parse_sample_map(path)
        self.assertIn("could not find a header row", str(caught.exception))

    def test_unsupported_extension_is_rejected(self):
        path = os.path.join(self.tmp, "sheet.pdf")
        self._write(path, "x")
        with self.assertRaises(blx.LongPlexInputError) as caught:
            blx.parse_sample_map(path)
        self.assertIn(".pdf", str(caught.exception))


class TestBarcodePaths(unittest.TestCase):
    def test_layout_is_flat_with_a_fa_extension(self):
        """Checked against LongPlex v3.1: no per-set subdirectory, not .fasta."""
        i7, i5 = blx.barcode_fasta_paths("/opt/longplex/barcodes", "set1")
        self.assertEqual(
            i7, "/opt/longplex/barcodes/LongPlex_set1_i7_trimmed_adapters.fa"
        )
        self.assertEqual(
            i5, "/opt/longplex/barcodes/LongPlex_set1_i5_trimmed_adapters.fa"
        )


class TestCli(LongPlexInputsTestCase):
    def test_empty_sample_map_argument_counts_as_absent(self):
        """The Marvin workflow templates the flag in unconditionally."""
        manifest = self._stage("longplex_pool_manifest_single.json")
        manifest_path = os.path.join(self.tmp, "manifest.json")
        self._write(manifest_path, json.dumps(manifest))

        exit_code = blx.main([
            "--pool-manifest", manifest_path,
            "--dest-dir", self.dest_dir,
            "--barcode-dir", self.barcode_dir,
            "--sample-map", "",
        ])
        self.assertEqual(exit_code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dest_dir, "rename_map.csv")))

    def test_validation_failure_exits_1_and_a_bad_manifest_exits_2(self):
        manifest = self._stage("longplex_pool_manifest.json", missing_pools=("bc1015",))
        manifest_path = os.path.join(self.tmp, "manifest.json")
        self._write(manifest_path, json.dumps(manifest))
        self.assertEqual(
            blx.main([
                "--pool-manifest", manifest_path,
                "--barcode-dir", self.barcode_dir,
            ]),
            1,
        )
        self.assertEqual(
            blx.main([
                "--pool-manifest", os.path.join(self.tmp, "absent.json"),
                "--barcode-dir", self.barcode_dir,
            ]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
