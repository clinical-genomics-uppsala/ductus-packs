"""Tests for scripts/run_longplex_demux.sh -- the LongPlex start script.

Plain unittest, no ST2 and no network:

    python3 -m unittest tests.test_run_longplex_demux_script -v

The script is executed for real, with a stub `nextflow` on PATH that records
the argv it was handed and exits with a code the test chooses. That is the
only way to assert on what actually reaches nextflow: the command line *is*
the interface, and the two things most likely to be wrong about it -- the
order of nextflow's own options relative to `run`, and which file lands on
--pool_sheet -- are both invisible to a syntax check.

Real temp directories and real (tiny) files are used rather than mocks, so
the existence and zero-byte pre-flight checks are genuinely exercised.

The stub also stands in for a module system: the script only calls `module`
if one is present, so these tests run on a laptop with no Lmod.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "run_longplex_demux.sh")

POOL_SHEET_HEADER = "pool_ID,pool_path,i7_barcode,i5_barcode"

# The sheet an operator is most likely to pass by mistake: it is what
# --sample-map means in both other PacBio scripts.
CLINICAL_SHEET_HEADER = "Project,Run_nr,Sample_ID,Index_ID"


class StartScriptTestCase(unittest.TestCase):
    def setUp(self):
        # realpath'd because macOS mkdtemp hands back a /var symlink into
        # /private/var, and the script echoes back the paths it was given.
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="longplex_demux_"))
        self.addCleanup(shutil.rmtree, self.root, True)

        self.inbox = self._mkdir("inbox")
        self.output = os.path.join(self.root, "output")
        self.bindir = self._mkdir("bin")
        self.argv_log = os.path.join(self.root, "nextflow_argv")

        # A pipeline directory is only credible if main.nf is in it: the
        # pipeline resolves schemas/input_schema.json relative to the project
        # dir, so a directory without main.nf cannot work.
        self.pipeline_dir = self._mkdir("longplex")
        self._write(os.path.join(self.pipeline_dir, "main.nf"), "// pipeline\n")

        self.pool_bam = self._write(
            os.path.join(self.inbox, "pool.hifi_reads.bam"), "BAMDATA"
        )
        self.i7 = self._write(
            os.path.join(self.inbox, "LongPlex_set1_i7_trimmed_adapters.fa"), ">i7\n"
        )
        self.i5 = self._write(
            os.path.join(self.inbox, "LongPlex_set1_i5_trimmed_adapters.fa"), ">i5\n"
        )
        self.pool_sheet = self.write_pool_sheet()

    # -- helpers ---------------------------------------------------------

    def _mkdir(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        return path

    def _write(self, path, content):
        with open(path, "w") as handle:
            handle.write(content)
        return path

    def write_pool_sheet(self, header=POOL_SHEET_HEADER, rows=None, name="pool_sheet.csv"):
        if rows is None:
            rows = ["bc1015,%s,%s,%s" % (self.pool_bam, self.i7, self.i5)]
        body = "\n".join([header] + list(rows)) + "\n"
        return self._write(os.path.join(self.inbox, name), body)

    def stub_nextflow(self, exit_code=0, on_path=True):
        """A recording `nextflow` on PATH. Returns nothing; read argv() after."""
        if not on_path:
            return
        path = os.path.join(self.bindir, "nextflow")
        self._write(
            path,
            "#!/bin/bash\n"
            'printf "%s\\n" "$@" > ' + self.argv_log + "\n"
            "exit %d\n" % exit_code,
        )
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def argv(self):
        if not os.path.exists(self.argv_log):
            return None
        with open(self.argv_log) as handle:
            return handle.read().splitlines()

    def run_script(self, *args, **kwargs):
        """Run the start script with only the stub dir plus coreutils on PATH."""
        env = dict(os.environ)
        # The stub dir first so `nextflow` resolves to the stub, and a
        # minimal real PATH after it so grep/awk/mkdir still work.
        env["PATH"] = self.bindir + ":/usr/bin:/bin:/usr/sbin:/sbin"
        env["LONGPLEX_PIPELINE_DIR"] = kwargs.pop("pipeline_dir", self.pipeline_dir)
        for key, value in (kwargs.pop("env", None) or {}).items():
            env[key] = value
        return subprocess.run(
            ["bash", SCRIPT] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=60,
        )

    def run_default(self, **kwargs):
        return self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            **kwargs
        )

    def assert_nextflow_not_run(self):
        self.assertIsNone(
            self.argv(),
            "nextflow was invoked; a pre-flight check should have stopped first",
        )

    def stderr(self, completed):
        return (completed.stderr or b"").decode("utf-8", "replace")


class ArgumentHandling(StartScriptTestCase):
    def test_no_arguments_prints_usage_and_fails(self):
        self.stub_nextflow()
        completed = self.run_script()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Usage:", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_the_three_flags_are_all_required(self):
        self.stub_nextflow()
        completed = self.run_script("--inbox-path", self.inbox)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Usage:", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_unknown_argument_is_refused(self):
        self.stub_nextflow()
        completed = self.run_default_with_extra("--sample-map", "/tmp/x.csv")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("--sample-map", self.stderr(completed))
        self.assert_nextflow_not_run()

    def run_default_with_extra(self, *extra):
        return self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            *extra
        )

    def test_analysis_path_is_accepted_as_an_alias_for_output(self):
        """The Miarka gateway's calling convention.

        start_analysis sends analysis_path as a first-class field, and the
        processing-service is assumed to pass it as --analysis-path (see
        docs/pacbio_reheader_miarka_contract.md). Accepting both forms is
        what lets one deployed script serve both clusters, which is the
        advice that contract gives.
        """
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--analysis-path", self.output,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertEqual(self.argv()[self.argv().index("--output") + 1], self.output)

    def test_conflicting_output_and_analysis_path_is_refused(self):
        """Two different destinations is a caller bug, not a preference.

        Picking one silently would write the run somewhere the caller is not
        looking for it.
        """
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--analysis-path", os.path.join(self.root, "elsewhere"),
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("elsewhere", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_the_same_destination_twice_is_fine(self):
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--analysis-path", self.output,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))

    def test_inbox_path_must_be_a_directory(self):
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", os.path.join(self.root, "nope"),
            "--samples-info", self.pool_sheet,
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("nope", self.stderr(completed))
        self.assert_nextflow_not_run()


class SamplesInfoValidation(StartScriptTestCase):
    def test_clinical_sample_sheet_is_refused_by_header(self):
        """The likeliest operator error, and the one worth catching early.

        --sample-map elsewhere in this pack means the clinical sheet. Passing
        it here would otherwise fail inside nextflow's schema validation,
        after a queue wait, with a message about pool_ID rather than about
        the wrong file.
        """
        self.stub_nextflow()
        sheet = self.write_pool_sheet(
            header=CLINICAL_SHEET_HEADER,
            rows=["proj007,Run2,CU01,B01"],
            name="sample_map.csv",
        )
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", sheet,
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 1)
        message = self.stderr(completed)
        self.assertIn("pool_ID", message)
        self.assertIn(sheet, message)
        self.assert_nextflow_not_run()

    def test_missing_samples_info_is_refused(self):
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", os.path.join(self.inbox, "absent.csv"),
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absent.csv", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_header_only_pool_sheet_is_refused(self):
        self.stub_nextflow()
        sheet = self.write_pool_sheet(rows=[], name="empty_pool_sheet.csv")
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", sheet,
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no data rows", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_column_order_is_not_assumed(self):
        """Header binding is by name, not position.

        build_longplex_inputs.py writes them in one fixed order, but a
        hand-made sheet with the same four columns in another order is valid
        input to the pipeline, so it must be valid here too.
        """
        self.stub_nextflow()
        sheet = self.write_pool_sheet(
            header="pool_path,pool_ID,i5_barcode,i7_barcode",
            rows=["%s,bc1015,%s,%s" % (self.pool_bam, self.i5, self.i7)],
            name="reordered.csv",
        )
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", sheet,
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertIn("--pool_sheet", self.argv())


class ReferencedFileValidation(StartScriptTestCase):
    def test_missing_pool_bam_is_refused(self):
        self.stub_nextflow()
        missing = os.path.join(self.inbox, "gone.hifi_reads.bam")
        self.write_pool_sheet(rows=["bc1015,%s,%s,%s" % (missing, self.i7, self.i5)])
        completed = self.run_default()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("gone.hifi_reads.bam", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_zero_byte_pool_bam_is_refused(self):
        """A truncated transfer leaves a real file of zero bytes.

        Existence alone would pass it through to lima, which would report
        something obscure hours later.
        """
        self.stub_nextflow()
        empty = self._write(os.path.join(self.inbox, "empty.hifi_reads.bam"), "")
        self.write_pool_sheet(rows=["bc1015,%s,%s,%s" % (empty, self.i7, self.i5)])
        completed = self.run_default()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("empty.hifi_reads.bam", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_missing_barcode_fasta_is_refused(self):
        self.stub_nextflow()
        self.write_pool_sheet(
            rows=[
                "bc1015,%s,%s,%s"
                % (self.pool_bam, os.path.join(self.inbox, "absent_i7.fa"), self.i5)
            ]
        )
        completed = self.run_default()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absent_i7.fa", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_every_row_is_checked_not_just_the_first(self):
        self.stub_nextflow()
        second = os.path.join(self.inbox, "second_pool_missing.bam")
        self.write_pool_sheet(
            rows=[
                "bc1015,%s,%s,%s" % (self.pool_bam, self.i7, self.i5),
                "bc1016,%s,%s,%s" % (second, self.i7, self.i5),
            ]
        )
        completed = self.run_default()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("second_pool_missing.bam", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_crlf_sheet_is_tolerated(self):
        """An Excel-exported sheet must not fail on a trailing \\r.

        The same defence scripts/reheader_pacbio_bams.sh has: without it the
        last column of every row carries a CR, so the i5 path would be
        reported missing when it is there.
        """
        self.stub_nextflow()
        body = "%s\r\nbc1015,%s,%s,%s\r\n" % (
            POOL_SHEET_HEADER, self.pool_bam, self.i7, self.i5,
        )
        sheet = self._write(os.path.join(self.inbox, "crlf.csv"), body)
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", sheet,
            "--output", self.output,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))


class PipelineResolution(StartScriptTestCase):
    def test_pipeline_dir_without_main_nf_is_refused(self):
        """main.nf must be the pipeline's own, in the pipeline's own tree.

        It calls samplesheetToList(params.pool_sheet,
        "schemas/input_schema.json") -- a project-relative path -- so a
        directory that merely exists is not enough.
        """
        self.stub_nextflow()
        bare = self._mkdir("bare")
        completed = self.run_default(pipeline_dir=bare)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("main.nf", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_missing_nextflow_on_path_fails_with_a_clear_message(self):
        self.stub_nextflow(on_path=False)
        completed = self.run_default()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("nextflow", self.stderr(completed))


class NextflowInvocation(StartScriptTestCase):
    def test_builds_the_expected_command(self):
        self.stub_nextflow()
        completed = self.run_default()
        self.assertEqual(completed.returncode, 0, self.stderr(completed))

        argv = self.argv()
        self.assertIn("run", argv)
        self.assertIn(os.path.join(self.pipeline_dir, "main.nf"), argv)
        self.assertIn("-resume", argv)
        self.assertEqual(argv[argv.index("--pool_sheet") + 1], self.pool_sheet)
        self.assertEqual(argv[argv.index("--output") + 1], self.output)
        self.assertEqual(argv[argv.index("-profile") + 1], "apptainer")
        self.assertEqual(
            argv[argv.index("-work-dir") + 1], os.path.join(self.output, "work")
        )

    def test_log_option_precedes_the_run_subcommand(self):
        """-log is a nextflow option, not a pipeline one.

        After `run` it would be passed to the workflow and rejected. This is
        the ordering mistake a syntax check cannot catch.
        """
        self.stub_nextflow()
        self.run_default()
        argv = self.argv()
        self.assertLess(argv.index("-log"), argv.index("run"))
        self.assertEqual(
            argv[argv.index("-log") + 1],
            os.path.join(self.output, "logs", "nextflow.log"),
        )

    def test_no_report_or_trace_flags_are_passed(self):
        """The pipeline's own nextflow.config already enables all four.

        trace/report/timeline/dag are configured to write into
        ${params.output}/logs/, so passing -with-report here would fight it.
        """
        self.stub_nextflow()
        self.run_default()
        argv = self.argv()
        for flag in ("-with-report", "-with-trace", "-with-timeline", "-with-dag"):
            self.assertNotIn(flag, argv)

    def test_profile_can_be_overridden(self):
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--profile", "singularity",
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertEqual(self.argv()[self.argv().index("-profile") + 1], "singularity")

    def test_site_config_is_passed_when_given(self):
        """The hook for a slurm executor.

        The pipeline ships apptainer/aws/conda/docker/singularity profiles and
        no executor config, so fanning out over a scheduler needs an extra -c.
        """
        self.stub_nextflow()
        site = self._write(os.path.join(self.root, "site.config"), "process {}\n")
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--site-config", site,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        argv = self.argv()
        self.assertEqual(argv[argv.index("-c") + 1], site)
        self.assertGreater(argv.index("-c"), argv.index("run"))

    def test_missing_site_config_is_refused(self):
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--site-config", os.path.join(self.root, "absent.config"),
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absent.config", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_no_site_config_means_no_dash_c(self):
        self.stub_nextflow()
        self.run_default()
        self.assertNotIn("-c", self.argv())

    def test_work_dir_can_be_overridden(self):
        """Nextflow's work dir holds full intermediate BAM copies.

        Somewhere with room for them is not always under --output.
        """
        self.stub_nextflow()
        elsewhere = os.path.join(self.root, "scratch_work")
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--work-dir", elsewhere,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertEqual(self.argv()[self.argv().index("-work-dir") + 1], elsewhere)


class RenameMap(StartScriptTestCase):
    """--rename-map, forwarded to the pipeline's optional --rename_map.

    Upstream LongPlex builds a pool_ID.well_ID -> sample_ID dict from this
    file and names MERGE_READS' outputs `${meta.sample_ID}.bam`, falling back
    to the pool.well key for any well the file does not mention. So a key
    that never matches is not an error to the pipeline -- it just silently
    keeps the default name. That is why the checks here are strict.

    Validated against schemas/rename_map_schema.json upstream:
      pool_ID.well_ID  ^[A-Za-z0-9]+\\.[A-H][0-9]{2}$
      sample_ID        ^\\S+$
    """

    RENAME_HEADER = "pool_ID.well_ID,sample_ID"

    def setUp(self):
        super(RenameMap, self).setUp()
        # A checkout that declares rename_map. The guard below refuses to
        # pass the flag to one that does not, so every test here needs it.
        self._write(
            os.path.join(self.pipeline_dir, "nextflow_schema.json"),
            '{"properties": {"rename_map": {"type": "string"}}}',
        )

    def write_rename_map(self, rows=None, header=None, name="rename_map.csv"):
        if rows is None:
            rows = ["bc1015.A01,CU01"]
        body = "\n".join([header or self.RENAME_HEADER] + list(rows)) + "\n"
        return self._write(os.path.join(self.inbox, name), body)

    def run_with_rename(self, rename_map, **kwargs):
        return self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--rename-map", rename_map,
            **kwargs
        )

    def test_it_is_forwarded_as_the_pipelines_rename_map_param(self):
        self.stub_nextflow()
        rename_map = self.write_rename_map()
        completed = self.run_with_rename(rename_map)
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        argv = self.argv()
        self.assertEqual(argv[argv.index("--rename_map") + 1], rename_map)

    def test_it_is_a_pipeline_param_not_a_nextflow_option(self):
        """--rename_map belongs after `run`, with --pool_sheet and --output."""
        self.stub_nextflow()
        self.run_with_rename(self.write_rename_map())
        argv = self.argv()
        self.assertGreater(argv.index("--rename_map"), argv.index("run"))

    def test_omitting_it_means_no_rename_map_param(self):
        self.stub_nextflow()
        self.run_default()
        self.assertNotIn("--rename_map", self.argv())

    def test_an_empty_value_means_not_supplied(self):
        """The st2 side always passes the flag; empty is how it says 'none'.

        Same convention as --work-dir and --site-config, so no workflow needs
        a YAQL conditional to leave the flag out.
        """
        self.stub_nextflow()
        completed = self.run_with_rename("")
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertNotIn("--rename_map", self.argv())

    def test_a_pipeline_without_rename_map_support_is_refused(self):
        """The silent-failure case this guard exists for.

        nextflow's failUnrecognisedParams defaults to false, so an older
        checkout accepts --rename_map, ignores it, and names every output
        bc1015.A01 instead of the sample. Nothing downstream would say why.
        """
        self.stub_nextflow()
        os.remove(os.path.join(self.pipeline_dir, "nextflow_schema.json"))
        self._write(
            os.path.join(self.pipeline_dir, "nextflow_schema.json"),
            '{"properties": {"pool_sheet": {"type": "string"}}}',
        )
        completed = self.run_with_rename(self.write_rename_map())
        self.assertEqual(completed.returncode, 1)
        message = self.stderr(completed)
        self.assertIn("rename_map", message)
        self.assert_nextflow_not_run()

    def test_a_missing_rename_map_is_refused(self):
        self.stub_nextflow()
        completed = self.run_with_rename(os.path.join(self.inbox, "absent_rename.csv"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("absent_rename.csv", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_the_wrong_header_is_refused(self):
        self.stub_nextflow()
        rename_map = self.write_rename_map(
            header="Project,Run_nr,Sample_ID,Index_ID",
            rows=["proj007,Run2,CU01,B01"],
        )
        completed = self.run_with_rename(rename_map)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("pool_ID.well_ID", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_header_only_rename_map_is_refused(self):
        self.stub_nextflow()
        completed = self.run_with_rename(self.write_rename_map(rows=[]))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no data rows", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_well_that_is_not_zero_padded_is_refused(self):
        """bc1015.A1 does not match the pipeline's own pattern.

        It would be accepted by our CSV reader, rejected by the pipeline's
        schema after the containers are pulled -- or worse, silently never
        match a well and leave that sample's output named bc1015.A01.
        """
        self.stub_nextflow()
        completed = self.run_with_rename(self.write_rename_map(rows=["bc1015.A1,CU01"]))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bc1015.A1", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_an_underscore_joined_key_is_refused(self):
        self.stub_nextflow()
        completed = self.run_with_rename(
            self.write_rename_map(rows=["bc1015_A01,CU01"])
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bc1015_A01", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_well_beyond_the_plate_is_refused(self):
        self.stub_nextflow()
        completed = self.run_with_rename(self.write_rename_map(rows=["bc1015.J01,CU01"]))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bc1015.J01", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_an_empty_sample_id_is_refused(self):
        self.stub_nextflow()
        completed = self.run_with_rename(self.write_rename_map(rows=["bc1015.A01,"]))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bc1015.A01", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_sample_id_containing_whitespace_is_refused(self):
        """The pipeline's pattern is ^\\S+$, and a space would break the
        output filename as well."""
        self.stub_nextflow()
        completed = self.run_with_rename(
            self.write_rename_map(rows=["bc1015.A01,CU 01"])
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("CU 01", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_duplicate_key_is_refused(self):
        """The pipeline builds a dict, so a repeated key silently last-wins.

        One well cannot be two samples; picking either is a coin toss on a
        clinical name.
        """
        self.stub_nextflow()
        completed = self.run_with_rename(
            self.write_rename_map(rows=["bc1015.A01,CU01", "bc1015.A01,CU02"])
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("bc1015.A01", self.stderr(completed))
        self.assert_nextflow_not_run()

    def test_a_key_naming_a_pool_that_is_not_in_the_pool_sheet_is_refused(self):
        """The failure the pipeline will not report.

        An unmatched key is not an error to the pipeline -- the well simply
        keeps its default pool.well name. So a typo'd or stale pool prefix
        produces a complete, successful run in which nothing was renamed.
        """
        self.stub_nextflow()
        completed = self.run_with_rename(self.write_rename_map(rows=["bc9999.A01,CU01"]))
        self.assertEqual(completed.returncode, 1)
        message = self.stderr(completed)
        self.assertIn("bc9999", message)
        self.assertIn("bc1015", message)
        self.assert_nextflow_not_run()

    def test_a_crlf_rename_map_is_tolerated(self):
        self.stub_nextflow()
        body = "%s\r\nbc1015.A01,CU01\r\n" % self.RENAME_HEADER
        rename_map = self._write(os.path.join(self.inbox, "crlf_rename.csv"), body)
        completed = self.run_with_rename(rename_map)
        self.assertEqual(completed.returncode, 0, self.stderr(completed))

    def test_underscores_are_allowed_in_a_sample_id(self):
        """Explicitly permitted upstream: 'underscores are accepted as
        connectors within the sample name'."""
        self.stub_nextflow()
        completed = self.run_with_rename(
            self.write_rename_map(rows=["bc1015.A01,CU_01_rerun"])
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))

    def test_a_flag_with_no_value_does_not_crash_the_parser(self):
        """`--rename-map` as the final token, which is what an empty
        interpolation into the Miarka gateway's parameters string produces.

        It must be a named error, not a `shift: count out of range`.
        """
        self.stub_nextflow()
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", self.output,
            "--rename-map",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("--rename-map", self.stderr(completed))
        self.assert_nextflow_not_run()


class OutputAndExitStatus(StartScriptTestCase):
    def test_output_directory_is_created(self):
        self.stub_nextflow()
        self.assertFalse(os.path.exists(self.output))
        completed = self.run_default()
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertTrue(os.path.isdir(self.output))
        self.assertTrue(os.path.isdir(os.path.join(self.output, "logs")))

    def test_nextflow_failure_is_propagated(self):
        """The exit code is the whole failure signal.

        core.remote's failed() and the gateway's job status both read it; a
        script that swallowed it would report a broken run as a success.
        """
        self.stub_nextflow(exit_code=42)
        completed = self.run_default()
        self.assertEqual(completed.returncode, 42)

    def test_success_is_exit_zero(self):
        self.stub_nextflow(exit_code=0)
        completed = self.run_default()
        self.assertEqual(completed.returncode, 0, self.stderr(completed))


class PathsWithSpaces(StartScriptTestCase):
    def test_a_path_containing_a_space_is_handled(self):
        """Quoting inside the script, independent of the caller's quoting.

        The st2 side single-quotes what it interpolates, but the script must
        not word-split what it receives either.
        """
        self.stub_nextflow()
        spaced_output = os.path.join(self.root, "out put")
        completed = self.run_script(
            "--inbox-path", self.inbox,
            "--samples-info", self.pool_sheet,
            "--output", spaced_output,
        )
        self.assertEqual(completed.returncode, 0, self.stderr(completed))
        self.assertTrue(os.path.isdir(spaced_output))
        self.assertEqual(self.argv()[self.argv().index("--output") + 1], spaced_output)


if __name__ == "__main__":
    unittest.main()
