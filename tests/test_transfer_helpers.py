"""Tests for lib/transfer.py -- the pure half of the BAM transfer.

Plain unittest, no ST2 and no network: lib/transfer.py imports neither, which
is the whole reason it is a separate module from the two actions that wrap it.
Runs without the st2 source tree:

    python3 -m unittest tests.test_transfer_helpers -v

Real temp directories and real (tiny) files are used rather than a fake
filesystem, so the existence, zero-byte and permission checks are actually
exercised. The script builder is asserted against as text, because text is
what gets executed -- including the quoting, which is the part that has to
hold when a path contains a space or a shell metacharacter.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from lib import transfer  # noqa: E402

BAM = "*.hifi_reads.bam"


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        # realpath'd: on macOS mkdtemp hands back a /var symlink into
        # /private/var, and resolve_source_files dedups by realpath, so
        # without this the expected paths would differ from the returned
        # ones for a reason that has nothing to do with the code.
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="transfer_test_"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_file(self, relpath, content=b"BAMDATA"):
        path = os.path.join(self.root, relpath)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as handle:
            handle.write(content)
        return path


class ResolveSourceFilesTests(TempDirTestCase):
    def test_a_file_given_directly_is_taken_regardless_of_pattern(self):
        # An explicit path is a decision the caller already made. Filtering it
        # by file_patterns would silently drop a BAM someone named by hand.
        odd = self.make_file("collection/reads.bam")
        self.assertEqual(transfer.resolve_source_files([odd], [BAM]), [odd])

    def test_directory_is_globbed_by_pattern(self):
        wanted = self.make_file("run/m84.hifi_reads.bam")
        self.make_file("run/m84.subreads.bam")
        self.make_file("run/m84.hifi_reads.bam.pbi")
        self.assertEqual(
            transfer.resolve_source_files([os.path.join(self.root, "run")], [BAM]),
            [wanted],
        )

    def test_collection_subdirectory_one_level_down_is_globbed(self):
        # Revio lays BAMs out per collection: <run>/1_A01/<movie>.hifi_reads.bam
        a = self.make_file("run/1_A01/m84_a.hifi_reads.bam")
        b = self.make_file("run/2_B01/m84_b.hifi_reads.bam")
        self.assertEqual(
            transfer.resolve_source_files([os.path.join(self.root, "run")], [BAM]),
            sorted([a, b]),
        )

    def test_two_levels_down_is_not_globbed(self):
        # The search is bounded on purpose: an unbounded walk of a run
        # directory can pick up demux scratch and previous-analysis copies.
        self.make_file("run/1_A01/deeper/m84.hifi_reads.bam")
        with self.assertRaises(transfer.TransferError):
            transfer.resolve_source_files([os.path.join(self.root, "run")], [BAM])

    def test_several_patterns_are_all_collected(self):
        bam = self.make_file("run/m84.hifi_reads.bam")
        pbi = self.make_file("run/m84.hifi_reads.bam.pbi")
        self.assertEqual(
            transfer.resolve_source_files(
                [os.path.join(self.root, "run")], [BAM, "*.hifi_reads.bam.pbi"]
            ),
            sorted([bam, pbi]),
        )

    def test_duplicates_collapse_by_realpath(self):
        real = self.make_file("run/m84.hifi_reads.bam")
        link = os.path.join(self.root, "link.hifi_reads.bam")
        os.symlink(real, link)
        self.assertEqual(
            transfer.resolve_source_files(
                [real, link, os.path.join(self.root, "run")], [BAM]
            ),
            [real],
        )

    def test_source_path_that_is_neither_file_nor_directory_raises(self):
        # The likely cause is the sequencing storage not being mounted on the
        # st2 host, which has to be a clear error rather than an empty list.
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.resolve_source_files([os.path.join(self.root, "nope")], [BAM])
        self.assertIn("nope", str(ctx.exception))

    def test_result_is_sorted(self):
        # The manifest order determines the rsync order and the expected.tsv
        # order, so it must not depend on readdir order.
        b = self.make_file("run/b.hifi_reads.bam")
        a = self.make_file("run/a.hifi_reads.bam")
        self.assertEqual(
            transfer.resolve_source_files([os.path.join(self.root, "run")], [BAM]),
            [a, b],
        )


class DefaultFilePatternsTests(TempDirTestCase):
    """The shipped default has to match a demultiplexed run too.

    The brief asserted that "*.hifi_reads.bam" covers both the undemuxed and
    the SMRT Link-demuxed case. It does not: fnmatch requires the name to
    *end* with .hifi_reads.bam, and a child dataset's BAM ends with
    .bcM####.bam. Left as it was, the demux path -- the LongPlex path this
    whole pipeline exists for -- would raise "no files matched" at transfer
    time on a run somebody was waiting for.
    """

    def resolved(self, *names):
        # A fresh collection directory per call: several of these tests probe
        # one name at a time in a loop, and a shared directory would let each
        # iteration see the previous one's file.
        self.collections = getattr(self, "collections", 0) + 1
        collection = "1_A%02d" % self.collections
        for name in names:
            self.make_file("%s/%s" % (collection, name))
        return [
            os.path.basename(path)
            for path in transfer.resolve_source_files(
                [os.path.join(self.root, collection)],
                transfer.DEFAULT_FILE_PATTERNS,
            )
        ]

    def test_an_undemultiplexed_hifi_bam_matches(self):
        self.assertEqual(
            self.resolved("m84189_250908.hifi_reads.bam"),
            ["m84189_250908.hifi_reads.bam"],
        )

    def test_a_smrt_link_demultiplexed_child_bam_matches(self):
        self.assertEqual(
            self.resolved("m84189_250908.hifi_reads.bcM0001.bam"),
            ["m84189_250908.hifi_reads.bcM0001.bam"],
        )

    def test_every_pool_of_a_demultiplexed_collection_matches(self):
        self.assertEqual(
            sorted(
                self.resolved(
                    "m84189.hifi_reads.bcM0001.bam",
                    "m84189.hifi_reads.bcM0002.bam",
                    "m84189.hifi_reads.bcM0003.bam",
                )
            ),
            [
                "m84189.hifi_reads.bcM0001.bam",
                "m84189.hifi_reads.bcM0002.bam",
                "m84189.hifi_reads.bcM0003.bam",
            ],
        )

    def test_any_hifi_reads_variant_matches(self):
        """Deliberately broader than the two names we know about.

        The default is "*.hifi_reads.bam" plus "*.hifi_reads.*.bam" rather
        than an enumeration of bcM####, bc1015--bc1015 and unassigned,
        because the exact set of infixes SMRT Link emits is a version detail
        nobody here has confirmed against a real demultiplexed run. Being one
        BAM too generous costs bytes; being one BAM short means a pool
        silently never arrives, and the basename-collision and zero-byte
        checks still hold either way.
        """
        for name in (
            "m84189.hifi_reads.unassigned.bam",
            "m84189.hifi_reads.bc1015--bc1015.bam",
            "m84189.hifi_reads.5mc.bam",
        ):
            self.assertEqual(self.resolved(name), [name])

    def test_sidecars_and_other_bam_flavours_are_still_excluded(self):
        # Nothing matched at all, which resolve_source_files reports as an
        # error rather than an empty transfer.
        for name in (
            "m84189.hifi_reads.bam.pbi",
            "m84189.hifi_reads.bcM0001.bam.pbi",
            "m84189.subreads.bam",
            "m84189.reads.bam",
            "m84189.consensusreadset.xml",
        ):
            with self.assertRaises(transfer.TransferError, msg=name):
                self.resolved(name)


class ValidateFilesTests(TempDirTestCase):
    def test_returns_basename_size_and_mtime(self):
        path = self.make_file("m84.hifi_reads.bam", b"1234567890")
        described = transfer.validate_files([path])
        self.assertEqual(len(described), 1)
        self.assertEqual(described[0]["path"], path)
        self.assertEqual(described[0]["basename"], "m84.hifi_reads.bam")
        self.assertEqual(described[0]["size"], 10)
        self.assertEqual(described[0]["mtime"], int(os.stat(path).st_mtime))

    def test_empty_list_raises(self):
        with self.assertRaises(transfer.TransferError):
            transfer.validate_files([])

    def test_missing_file_raises(self):
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.validate_files([os.path.join(self.root, "gone.hifi_reads.bam")])
        self.assertIn("gone.hifi_reads.bam", str(ctx.exception))

    def test_zero_byte_file_raises(self):
        empty = self.make_file("empty.hifi_reads.bam", b"")
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.validate_files([empty])
        self.assertIn("empty", str(ctx.exception))

    @unittest.skipIf(os.geteuid() == 0, "root bypasses the read permission bit")
    def test_unreadable_file_raises(self):
        path = self.make_file("locked.hifi_reads.bam")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, stat.S_IRUSR | stat.S_IWUSR)
        with self.assertRaises(transfer.TransferError):
            transfer.validate_files([path])

    def test_basename_collision_raises(self):
        # The destination is flat (--no-relative), so two same-named BAMs from
        # different collections would silently overwrite one another.
        first = self.make_file("1_A01/m84.hifi_reads.bam")
        second = self.make_file("2_B01/m84.hifi_reads.bam")
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.validate_files([first, second])
        message = str(ctx.exception)
        self.assertIn("m84.hifi_reads.bam", message)
        self.assertIn(first, message)
        self.assertIn(second, message)

    def test_newline_in_path_raises(self):
        # rsync --files-from is newline-delimited and expected.tsv is
        # tab-delimited, so either character in a path corrupts the transfer
        # into copying something other than what was validated.
        path = self.make_file("two\nlines.hifi_reads.bam")
        with self.assertRaises(transfer.TransferError):
            transfer.validate_files([path])

    def test_tab_in_path_raises(self):
        path = self.make_file("has\ttab.hifi_reads.bam")
        with self.assertRaises(transfer.TransferError):
            transfer.validate_files([path])


class ResolveDestinationTests(unittest.TestCase):
    """Switching cluster must be a config change and nothing else.

    Which is why every cluster-shaped value -- host, user, key, root, bandwidth
    ceiling, extra ssh and rsync options -- is read from here, and why an
    unusable destination is an error at resolve time rather than a broken
    rsync command line half an hour later.
    """

    def config(self, **overrides):
        base = {
            "default_destination": "marvin",
            "destinations": {
                "marvin": {
                    "host": "marvin.example.se",
                    "user": "stanley",
                    "ssh_key_path": "/home/stanley/.ssh/id_rsa",
                    "dest_root": "/scratch/pacbio/runs",
                },
                "miarka": {
                    "host": "miarka1.uppmax.uu.se",
                    "user": "stanley",
                    "ssh_key_path": "/home/stanley/.ssh/id_miarka",
                    "dest_root": "/proj/ngi2024001/nobackup/pacbio",
                    "bwlimit": "100M",
                    "ssh_extra_opts": ["-o", "IPQoS=throughput"],
                    "rsync_extra": ["--copy-links"],
                },
            },
        }
        base.update(overrides)
        return base

    def test_named_destination_is_used(self):
        dest = transfer.resolve_destination(self.config(), "miarka")
        self.assertEqual(dest["name"], "miarka")
        self.assertEqual(dest["host"], "miarka1.uppmax.uu.se")
        self.assertEqual(dest["dest_root"], "/proj/ngi2024001/nobackup/pacbio")
        self.assertEqual(dest["bwlimit"], "100M")
        self.assertEqual(dest["ssh_extra_opts"], ["-o", "IPQoS=throughput"])
        self.assertEqual(dest["rsync_extra"], ["--copy-links"])

    def test_default_destination_is_used_when_none_given(self):
        for empty in (None, ""):
            dest = transfer.resolve_destination(self.config(), empty)
            self.assertEqual(dest["name"], "marvin")

    def test_defaults_are_filled_in(self):
        dest = transfer.resolve_destination(self.config(), "marvin")
        self.assertEqual(dest["bwlimit"], transfer.DEFAULT_BWLIMIT)
        self.assertEqual(dest["ssh_extra_opts"], [])
        self.assertEqual(dest["rsync_extra"], [])
        self.assertEqual(dest["transfer_method"], "rsync")

    def test_unknown_destination_names_the_ones_that_exist(self):
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.resolve_destination(self.config(), "rackham")
        message = str(ctx.exception)
        self.assertIn("rackham", message)
        self.assertIn("marvin", message)
        self.assertIn("miarka", message)

    def test_missing_required_key_raises(self):
        config = self.config()
        del config["destinations"]["marvin"]["dest_root"]
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.resolve_destination(config, "marvin")
        self.assertIn("dest_root", str(ctx.exception))

    def test_no_destinations_configured_raises(self):
        with self.assertRaises(transfer.TransferError):
            transfer.resolve_destination({"destinations": {}}, "marvin")

    def test_unsupported_transfer_method_raises(self):
        # transfer_method exists so Globus can be added later. Until it is,
        # a destination asking for it must fail rather than quietly rsync.
        config = self.config()
        config["destinations"]["miarka"]["transfer_method"] = "globus"
        with self.assertRaises(transfer.TransferError) as ctx:
            transfer.resolve_destination(config, "miarka")
        self.assertIn("globus", str(ctx.exception))


class BuildSshCommandTests(unittest.TestCase):
    def test_batch_mode_and_keepalives_are_set(self):
        argv = transfer.build_ssh_command("/home/stanley/.ssh/id_rsa")
        joined = " ".join(argv)
        self.assertIn("-i /home/stanley/.ssh/id_rsa", joined)
        # BatchMode: an action running unattended must fail rather than block
        # on a passphrase or a host-key question.
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=accept-new", joined)
        # Keepalives: a multi-hour transfer through a stateful firewall gets
        # its idle control channel dropped without them.
        self.assertIn("ServerAliveInterval=30", joined)
        self.assertIn("ServerAliveCountMax=6", joined)

    def test_extra_opts_are_appended(self):
        argv = transfer.build_ssh_command("/k", ["-o", "IPQoS=throughput"])
        self.assertEqual(argv[-2:], ["-o", "IPQoS=throughput"])

    def test_key_path_containing_whitespace_raises(self):
        # rsync word-splits its own -e argument, so a key path with a space in
        # it would reach ssh as two broken options. Refuse it by name instead
        # of emitting a command line that cannot work.
        with self.assertRaises(transfer.TransferError):
            transfer.build_ssh_command("/home/st anley/.ssh/id_rsa")

    def test_no_target_is_included(self):
        # The result is the -e argument for rsync and the prefix of the
        # verification calls, so it must not name a host.
        self.assertNotIn("@", " ".join(transfer.build_ssh_command("/k")))


class ProbeTests(unittest.TestCase):
    """The probe is how a rerun decides it has nothing to do.

    Both output samples below are real captured rsync output (rsync 2.6.9
    protocol, `-n -i -rlpt --size-only --no-relative --files-from=...`), not
    invented: identical trees print nothing at all, and a destination file
    truncated to zero prints one itemized line.
    """

    IDENTICAL = ""
    TRUNCATED = ">f.st.... a.hifi_reads.bam\n"

    def test_size_mode_compares_sizes_only(self):
        argv = transfer.build_probe_argv(
            "/staging/manifest.txt", "stanley@marvin:/scratch/run/", ["ssh", "-i", "/k"]
        )
        self.assertIn("-n", argv)
        self.assertIn("-i", argv)
        self.assertIn("--size-only", argv)
        self.assertNotIn("--checksum", argv)
        self.assertIn("--no-relative", argv)
        # Same remote-path exposure as the real transfer: the probe's
        # destination goes through the remote shell without it.
        self.assertIn("--protect-args", argv)
        self.assertIn("--files-from=/staging/manifest.txt", argv)
        self.assertEqual(argv[-2:], ["/", "stanley@marvin:/scratch/run/"])
        self.assertIn("-e", argv)
        self.assertIn("ssh -i /k", argv)

    def test_checksum_mode_replaces_size_only(self):
        argv = transfer.build_probe_argv(
            "/staging/manifest.txt",
            "stanley@marvin:/scratch/run/",
            ["ssh"],
            mode="checksum",
        )
        self.assertIn("--checksum", argv)
        self.assertNotIn("--size-only", argv)

    def test_unknown_mode_raises(self):
        with self.assertRaises(transfer.TransferError):
            transfer.build_probe_argv("/m", "h:/d", ["ssh"], mode="mtime")

    def test_probe_is_always_a_dry_run(self):
        # A probe that could write is not a probe. -n guards the idempotency
        # check against ever being the thing that copies terabytes.
        for mode in ("size", "checksum"):
            self.assertIn("-n", transfer.build_probe_argv("/m", "h:/d", ["ssh"], mode))

    def test_identical_trees_report_no_differences(self):
        self.assertFalse(transfer.probe_reports_differences(self.IDENTICAL))

    def test_truncated_destination_file_reports_a_difference(self):
        self.assertTrue(transfer.probe_reports_differences(self.TRUNCATED))

    def test_attribute_only_difference_is_not_a_difference(self):
        # A permission or timestamp change is not a corrupt BAM, and the
        # transfer sets both anyway (--chmod, -t). Treating it as a difference
        # would recopy hundreds of gigabytes over a metadata mismatch.
        self.assertFalse(transfer.probe_reports_differences(".f...p... a.bam\n"))

    def test_noise_lines_are_ignored(self):
        self.assertFalse(
            transfer.probe_reports_differences(
                "sending incremental file list\n\nsent 191 bytes\n"
            )
        )


DESTINATION = {
    "name": "marvin",
    "host": "marvin.example.se",
    "user": "stanley",
    "ssh_key_path": "/home/stanley/.ssh/id_rsa",
    "dest_root": "/scratch/pacbio/runs",
    "bwlimit": "200M",
    "ssh_extra_opts": [],
    "rsync_extra": [],
    "transfer_method": "rsync",
}

FILES = [
    {
        "path": "/data/revio/run/1_A01/m84.hifi_reads.bam",
        "basename": "m84.hifi_reads.bam",
        "size": 1234,
        "mtime": 0,
    },
    {
        "path": "/data/revio/run/2_B01/m85.hifi_reads.bam",
        "basename": "m85.hifi_reads.bam",
        "size": 5678,
        "mtime": 0,
    },
]


class RemoteAddressTests(unittest.TestCase):
    def test_target_is_user_at_host(self):
        self.assertEqual(transfer.remote_target(DESTINATION), "stanley@marvin.example.se")

    def test_spec_has_a_trailing_slash(self):
        # rsync treats a destination without a trailing slash as a filename
        # when a single file is transferred, which would rename the BAM to the
        # run directory's name.
        self.assertEqual(
            transfer.remote_spec(DESTINATION, "/scratch/pacbio/runs/r84"),
            "stanley@marvin.example.se:/scratch/pacbio/runs/r84/",
        )

    def test_spec_does_not_double_a_trailing_slash(self):
        self.assertEqual(
            transfer.remote_spec(DESTINATION, "/scratch/pacbio/runs/r84/"),
            "stanley@marvin.example.se:/scratch/pacbio/runs/r84/",
        )


class BuildPrepareArgvTests(unittest.TestCase):
    """Creating the destination and clearing our own stale sentinels.

    One round trip, because it is two things that must both be true before
    rsync starts: the directory exists (--mkpath needs rsync 3.2.3, which is
    not guaranteed on either cluster) and no previous attempt's
    .transfer_complete / .transfer_failed is lying about this one.
    """

    def command(self, **kwargs):
        argv = transfer.build_prepare_argv(
            transfer.build_ssh_command("/k"), DESTINATION,
            "/scratch/pacbio/runs/r84", **kwargs
        )
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-2], "stanley@marvin.example.se")
        return argv[-1]

    def test_destination_directory_is_created(self):
        self.assertIn("mkdir -p '/scratch/pacbio/runs/r84'", self.command())

    def test_both_stale_sentinels_are_removed(self):
        command = self.command()
        self.assertIn("/scratch/pacbio/runs/r84/.transfer_complete", command)
        self.assertIn("/scratch/pacbio/runs/r84/.transfer_failed", command)

    def test_only_the_two_sentinels_are_removed(self):
        # The one deliberate exception to "no rm against the destination":
        # these are this action's own control files, and a stale one makes
        # wait_for_transfer report a previous run's outcome for this one.
        # Nothing else may be named, and no glob may be used.
        command = self.command()
        self.assertNotIn("*", command)
        self.assertNotIn("-r", command)
        self.assertEqual(command.count("rm -f"), 1)

    def test_a_dry_run_removes_nothing(self):
        self.assertNotIn("rm", self.command(clear_sentinels=False))

    def test_dest_path_is_quoted_for_the_remote_shell(self):
        import shlex
        nasty = "/scratch/pacbio/runs/r84; rm -rf /tmp"
        argv = transfer.build_prepare_argv(
            transfer.build_ssh_command("/k"), DESTINATION, nasty
        )
        # The remote command is one ssh argument; splitting it the way the
        # remote shell would must give back the path as a single token.
        tokens = shlex.split(argv[-1])
        self.assertIn(nasty, tokens)


class BuildTransferScriptTests(unittest.TestCase):
    def script(self, **kwargs):
        params = {
            "staging_dir": "/var/lib/st2/ductus/transfers/r84.marvin",
            "files": FILES,
            "destination": DESTINATION,
            "dest_path": "/scratch/pacbio/runs/r84",
        }
        params.update(kwargs)
        return transfer.build_transfer_script(**params)

    def test_is_valid_bash(self):
        self.assertTrue(self.script().startswith("#!/bin/bash"))
        self.assertBashParses(self.script())

    def assertBashParses(self, script):
        import subprocess
        import tempfile as tf
        with tf.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(script)
            path = handle.name
        self.addCleanup(os.unlink, path)
        result = subprocess.run(
            ["bash", "-n", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(
            result.returncode, 0, result.stderr.decode("utf-8", "replace")
        )

    def test_deliberate_rsync_flags_are_present(self):
        script = self.script()
        # -rlptv, not -a: -a implies -og, which on a cluster where the service
        # account is not root either fails or carries restrictive
        # instrument-storage ownership across.
        for flag in (
            "-rlptv",
            "--no-o",
            "--no-g",
            # so pipeline group members can read the BAMs
            "--chmod=Dg+rwx,Fg+r",
            # resume a dropped WAN link instead of restarting
            "--partial",
            "--partial-dir=.rsync-partial",
            "--bwlimit=200M",
            # with the ssh keepalives, for stateful firewalls
            "--timeout=600",
            "--info=progress2,stats2",
            # flattens the absolute source paths into dest_path
            "--no-relative",
            "--files-from='/var/lib/st2/ductus/transfers/r84.marvin/manifest.txt'",
        ):
            self.assertIn(flag, script, "missing rsync flag %s" % flag)

    def test_forbidden_flags_are_absent(self):
        script = self.script()
        # --delete/--remove-source-files: nothing here may destroy data on
        # either side. --inplace: a killed transfer would leave a
        # plausible-looking corrupt BAM at the final filename. -a: see above.
        for flag in ("--delete", "--remove-source-files", "--inplace", "--archive"):
            self.assertNotIn(flag, script, "forbidden rsync flag %s" % flag)
        self.assertNotIn(" -a ", script)

    def test_bwlimit_can_be_overridden(self):
        script = self.script(bwlimit="50M")
        self.assertIn("--bwlimit=50M", script)
        self.assertNotIn("--bwlimit=200M", script)

    def test_destination_rsync_extra_is_included(self):
        destination = dict(DESTINATION, rsync_extra=["--copy-links"])
        self.assertIn("--copy-links", self.script(destination=destination))

    def test_sentinel_is_written_only_after_verification(self):
        script = self.script()
        rsync_at = script.index("rsync ")
        verify_at = script.index("stat -c")
        complete_at = script.index(transfer.SENTINEL_COMPLETE)
        self.assertLess(rsync_at, verify_at)
        self.assertLess(verify_at, complete_at)

    def test_verification_compares_expected_sizes(self):
        script = self.script()
        self.assertIn("expected.tsv", script)
        self.assertIn("actual.tsv", script)
        self.assertIn("m84.hifi_reads.bam", script)
        self.assertIn("m85.hifi_reads.bam", script)

    def test_failure_records_a_failed_sentinel(self):
        script = self.script()
        self.assertIn(transfer.SENTINEL_FAILED, script)

    def test_a_nonzero_rsync_exit_is_not_swallowed(self):
        # No 'set -e': the exit code is captured and acted on deliberately,
        # and the failure branch has to run rather than abort the script.
        script = self.script()
        self.assertNotIn("set -e", script)
        self.assertIn("set -uo pipefail", script)
        self.assertIn("rc=$?", script)

    def test_dry_run_adds_n(self):
        self.assertIn(" -n ", self.script(dry_run=True))

    def test_dry_run_neither_verifies_nor_writes_a_sentinel(self):
        # With -n nothing lands, so a size check would fail and a sentinel
        # would be a lie.
        script = self.script(dry_run=True)
        self.assertNotIn("stat -c", script)
        self.assertNotIn(transfer.SENTINEL_COMPLETE, script)
        self.assertNotIn("touch", script)
        self.assertBashParses(script)

    def test_checksum_verify_adds_a_second_probe(self):
        # Size comparison catches truncation, the realistic failure mode after
        # a clean rsync exit, without re-reading both copies end to end.
        # --checksum is the opt-in for when that is worth paying for.
        script = self.script(verify="checksum")
        self.assertIn("--checksum", script)
        self.assertIn("stat -c", script)
        self.assertBashParses(script)

    def test_size_verify_does_not_checksum(self):
        self.assertNotIn("--checksum", self.script())

    def test_unknown_verify_mode_raises(self):
        with self.assertRaises(transfer.TransferError):
            self.script(verify="md5")

    def test_injected_shell_metacharacters_stay_data(self):
        import shlex
        nasty_dest = "/scratch/pacbio/runs/r84; rm -rf /tmp"
        nasty_staging = "/var/lib/st2/ductus/transfers/with space/$(whoami)"
        script = self.script(dest_path=nasty_dest, staging_dir=nasty_staging)
        self.assertBashParses(script)
        # Locally: every interpolation is a single quoted bash word.
        self.assertIn(shlex.quote(nasty_dest), script)
        self.assertIn(shlex.quote(os.path.join(nasty_staging, "manifest.txt")), script)
        # Remotely: the far-side command is one ssh argument, and splitting it
        # the way the remote shell would must give the path back intact.
        for line in script.splitlines():
            if "stat -c" in line:
                remote = [t for t in shlex.split(line) if "stat -c" in t][0]
                self.assertIn(nasty_dest, shlex.split(remote))
                break
        else:
            self.fail("no verification line found")

    def test_basenames_with_spaces_survive_the_far_side_command(self):
        import shlex
        files = [dict(FILES[0], basename="m84 copy.hifi_reads.bam")]
        script = self.script(files=files)
        self.assertBashParses(script)
        for line in script.splitlines():
            if "stat -c" in line:
                remote = [t for t in shlex.split(line) if "stat -c" in t][0]
                self.assertIn("m84 copy.hifi_reads.bam", shlex.split(remote))
                break
        else:
            self.fail("no verification line found")


class BwlimitTests(unittest.TestCase):
    """bwlimit is validated, not quoted.

    It is interpolated bare into the rsync command line (--bwlimit=200M), so
    it is the one interpolation that cannot be a quoted shell word. Checking
    its shape is what keeps that safe, and it also turns a typo in pack config
    into a clear error instead of an rsync usage message inside a detached
    log.
    """

    def test_plain_and_suffixed_rates_are_accepted(self):
        for limit in ("200M", "1G", "500", "12.5M", "100KiB", "0"):
            script = transfer.build_transfer_script(
                "/staging", FILES, DESTINATION, "/dest", bwlimit=limit
            )
            self.assertIn("--bwlimit=%s" % limit, script)

    def test_a_rate_that_is_not_a_rate_raises(self):
        for limit in ("200M; rm -rf /", "$(whoami)", "fast", "200 M", "-1"):
            with self.assertRaises(transfer.TransferError):
                transfer.build_transfer_script(
                    "/staging", FILES, DESTINATION, "/dest", bwlimit=limit
                )


class ValidateDestSubdirTests(unittest.TestCase):
    """The leaf directory name is rejected, not sanitised.

    Quoting already keeps a hostile run_name from becoming a command, but the
    name is also a directory that people and downstream actions have to use,
    so a name that needs quoting is a name someone should look at. Rejecting
    beats silently rewriting: a rewritten name no longer matches the run it
    came from.
    """

    def test_ordinary_run_names_pass(self):
        for name in ("r84189_20260908_075306", "m84189-1_A01", "run.2", "A_b-1.2"):
            self.assertEqual(transfer.validate_dest_subdir(name), name)

    def test_path_traversal_is_rejected(self):
        for name in ("..", "../etc", "a/b", "/abs", "."):
            with self.assertRaises(transfer.TransferError):
                transfer.validate_dest_subdir(name)

    def test_shell_metacharacters_are_rejected(self):
        for name in ("r84; rm -rf /", "$(whoami)", "a b", "a*", "a\nb", "a'b"):
            with self.assertRaises(transfer.TransferError):
                transfer.validate_dest_subdir(name)

    def test_leading_dash_is_rejected(self):
        # A leading dash makes the directory name look like an option to
        # anything that later handles it positionally.
        with self.assertRaises(transfer.TransferError):
            transfer.validate_dest_subdir("-rf")

    def test_empty_is_rejected(self):
        for name in ("", None):
            with self.assertRaises(transfer.TransferError):
                transfer.validate_dest_subdir(name)


class SentinelCommandTests(unittest.TestCase):
    """The two read-only far-side questions wait_for_transfer and the
    idempotency check are built from."""

    SSH = ["ssh", "-i", "/k"]

    def test_check_asks_test_f_for_the_completion_sentinel(self):
        argv = transfer.build_sentinel_check_argv(
            self.SSH, DESTINATION, "/scratch/pacbio/runs/r84"
        )
        self.assertEqual(argv[:3], ["ssh", "-i", "/k"])
        self.assertEqual(argv[-2], "stanley@marvin.example.se")
        self.assertEqual(
            argv[-1], "test -f '/scratch/pacbio/runs/r84/.transfer_complete'"
        )

    def test_check_can_ask_about_the_failure_sentinel(self):
        argv = transfer.build_sentinel_check_argv(
            self.SSH, DESTINATION, "/scratch/pacbio/runs/r84",
            sentinel=transfer.SENTINEL_FAILED,
        )
        self.assertIn(".transfer_failed", argv[-1])

    def test_read_cats_the_sentinel(self):
        argv = transfer.build_sentinel_read_argv(
            self.SSH, DESTINATION, "/scratch/pacbio/runs/r84",
            sentinel=transfer.SENTINEL_FAILED,
        )
        self.assertEqual(
            argv[-1], "cat '/scratch/pacbio/runs/r84/.transfer_failed'"
        )

    def test_the_path_is_quoted_for_the_remote_shell(self):
        import shlex
        nasty = "/scratch/pacbio/runs/r84; rm -rf /tmp"
        for builder in (
            transfer.build_sentinel_check_argv,
            transfer.build_sentinel_read_argv,
        ):
            argv = builder(self.SSH, DESTINATION, nasty)
            self.assertIn(
                nasty + "/" + transfer.SENTINEL_COMPLETE, shlex.split(argv[-1])
            )


class StatusCommandTests(unittest.TestCase):
    """One round trip per poll, answering everything a waiter needs.

    Asking "is it done", then "did it fail", then "why" would be three ssh
    connections per poll -- 1400+ over an eight-hour wait, against a cluster
    login node that may well be logging and rate-limiting them.
    """

    def argv(self):
        return transfer.build_status_argv(
            ["ssh", "-i", "/k"], DESTINATION, "/scratch/pacbio/runs/r84"
        )

    def test_both_sentinels_are_examined(self):
        command = self.argv()[-1]
        self.assertIn("/scratch/pacbio/runs/r84/.transfer_complete", command)
        self.assertIn("/scratch/pacbio/runs/r84/.transfer_failed", command)

    def test_completion_is_reported_before_failure(self):
        # A rerun clears both sentinels, but if the two ever coexist, the
        # verified copy is the truth.
        command = self.argv()[-1]
        self.assertLess(
            command.index(transfer.SENTINEL_COMPLETE),
            command.index(transfer.SENTINEL_FAILED),
        )

    def test_the_reason_comes_back_in_the_same_round_trip(self):
        self.assertIn("cat", self.argv()[-1])

    def test_nothing_is_written(self):
        command = self.argv()[-1]
        for destructive in ("rm ", "touch", "mkdir", ">"):
            self.assertNotIn(destructive, command)

    def test_the_path_is_quoted_for_the_remote_shell(self):
        import shlex
        nasty = "/scratch/runs/r84; rm -rf /tmp"
        argv = transfer.build_status_argv(["ssh"], DESTINATION, nasty)
        # Split the way the remote shell would: each sentinel path has to come
        # back as one word, so the ';' stays inside a filename instead of
        # ending the command.
        tokens = shlex.split(argv[-1])
        self.assertIn(nasty + "/" + transfer.SENTINEL_COMPLETE, tokens)
        self.assertIn(nasty + "/" + transfer.SENTINEL_FAILED, tokens)


class ParseStatusTests(unittest.TestCase):
    def test_complete(self):
        self.assertEqual(transfer.parse_status_output("COMPLETE\n"), ("complete", ""))

    def test_pending(self):
        self.assertEqual(transfer.parse_status_output("PENDING\n"), ("pending", ""))

    def test_failed_carries_the_reason(self):
        self.assertEqual(
            transfer.parse_status_output("FAILED\nrsync exit 23\n"),
            ("failed", "rsync exit 23"),
        )

    def test_failed_with_an_unreadable_reason_is_still_failed(self):
        self.assertEqual(transfer.parse_status_output("FAILED\n"), ("failed", ""))

    def test_unrecognised_output_is_not_silently_pending(self):
        # An ssh banner or a login-shell message would otherwise read as
        # "still going" for the whole timeout.
        for output in ("", "Welcome to Miarka\n", "\n"):
            with self.assertRaises(transfer.TransferError):
                transfer.parse_status_output(output)

    def test_a_banner_before_the_answer_is_tolerated(self):
        # Login shells on cluster nodes do print things.
        self.assertEqual(
            transfer.parse_status_output("Last login: Mon\nCOMPLETE\n"),
            ("complete", ""),
        )


class TailLinesTests(TempDirTestCase):
    def test_returns_the_last_n_lines(self):
        path = self.make_file(
            "transfer.log", ("\n".join("line %d" % i for i in range(100)) + "\n").encode()
        )
        tail = transfer.tail_lines(path, 5)
        self.assertEqual(tail.splitlines(), ["line %d" % i for i in range(95, 100)])

    def test_a_short_file_comes_back_whole(self):
        path = self.make_file("transfer.log", b"only line\n")
        self.assertEqual(transfer.tail_lines(path, 40).strip(), "only line")

    def test_a_missing_file_is_not_an_error(self):
        # The log lives on the st2 host; a caller may be waiting on a transfer
        # launched by an execution whose staging directory is gone. Reporting
        # a timeout matters more than reporting a missing log.
        self.assertEqual(transfer.tail_lines(os.path.join(self.root, "nope"), 40), "")

    def test_no_path_is_not_an_error(self):
        self.assertEqual(transfer.tail_lines(None, 40), "")

    def test_undecodable_bytes_do_not_raise(self):
        # rsync --info=progress2 writes carriage returns and can be cut
        # mid-sequence when a transfer is killed.
        path = self.make_file("transfer.log", b"fine\n\xff\xfe not utf8\n")
        self.assertIn("fine", transfer.tail_lines(path, 40))


class PlanPathsTests(unittest.TestCase):
    """Every path a transfer uses, derived from config and the leaf name.

    One function, used by both actions, so ductus.wait_for_transfer can work
    out where to look without being handed values through the workflow. That
    removes a data dependency between two tasks in favour of both deriving
    from the same config -- and config is the thing that must agree anyway.
    """

    SETTINGS = {
        "staging_dir": "/var/lib/st2/ductus/transfers",
        "destinations": {"marvin": {}},
    }

    def plan(self, settings=None, leaf="r84189_20260908"):
        return transfer.plan_paths(
            self.SETTINGS if settings is None else settings, DESTINATION, leaf
        )

    def test_destination_path_is_the_root_plus_the_leaf(self):
        self.assertEqual(
            self.plan()["dest_path"], "/scratch/pacbio/runs/r84189_20260908"
        )

    def test_a_trailing_slash_on_the_root_is_not_doubled(self):
        destination = dict(DESTINATION, dest_root="/scratch/pacbio/runs/")
        plan = transfer.plan_paths(self.SETTINGS, destination, "r84")
        self.assertEqual(plan["dest_path"], "/scratch/pacbio/runs/r84")

    def test_staging_is_keyed_by_leaf_and_destination(self):
        plan = self.plan()
        # Keyed by both: the same run may legitimately be in flight to two
        # clusters at once, and those two transfers must not share a lock, a
        # manifest or a log.
        self.assertEqual(
            plan["staging_dir"],
            "/var/lib/st2/ductus/transfers/r84189_20260908.marvin",
        )
        self.assertEqual(
            plan["lock_path"],
            "/var/lib/st2/ductus/transfers/r84189_20260908.marvin.lock",
        )

    def test_the_log_lives_in_the_staging_directory(self):
        plan = self.plan()
        self.assertEqual(
            plan["log_path"], os.path.join(plan["staging_dir"], "transfer.log")
        )

    def test_the_sentinel_path_is_on_the_destination(self):
        self.assertEqual(
            self.plan()["sentinel_path"],
            "/scratch/pacbio/runs/r84189_20260908/.transfer_complete",
        )

    def test_a_missing_staging_dir_setting_falls_back_to_the_default(self):
        plan = self.plan(settings={"destinations": {"marvin": {}}})
        self.assertTrue(plan["staging_dir"].startswith(transfer.DEFAULT_STAGING_DIR))


# Shims standing in for the three programs the generated script calls. They
# are the smallest thing that makes the script's own logic observable without
# a network or a cluster.
SSH_SHIM = r"""#!/bin/bash
# A remote shell that happens to be this one. sshd runs the command it is
# handed as a single string through a shell, and so does this -- which is
# exactly the layer the generated script has to quote correctly for.
echo "SSH $*" >> "$SHIM_TRACE"
cmd="${@: -1}"
PATH="$SHIM_BIN:$PATH" exec bash -c "$cmd"
"""

RSYNC_SHIM = r"""#!/bin/bash
echo "RSYNC $*" >> "$SHIM_TRACE"
if [ -n "${SHIM_RSYNC_RC:-}" ]; then exit "$SHIM_RSYNC_RC"; fi
manifest=""
dry=0
for arg in "$@"; do
  case "$arg" in
    --files-from=*) manifest="${arg#--files-from=}" ;;
    -n) dry=1 ;;
  esac
done
dest="${@: -1}"
dest="${dest#*:}"
[ "$dry" = 1 ] && exit 0
while IFS= read -r file; do
  base=$(basename "$file")
  cp "$file" "$dest/$base"
  if [ "${SHIM_TRUNCATE:-}" = "$base" ]; then : > "$dest/$base"; fi
done < "$manifest"
"""

STAT_SHIM = r"""#!/bin/bash
# Implements only what the generated script asks for. The format string is
# checked rather than ignored: if it did not survive two layers of quoting
# intact, this fails loudly instead of quietly reporting the wrong thing.
fmt=""
while [ $# -gt 0 ]; do
  case "$1" in
    -c) fmt="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
if [ "$fmt" != '%n\t%s' ]; then
  echo "stat shim got format '$fmt', expected '%n\t%s'" >&2
  exit 64
fi
for name in "$@"; do
  printf '%s\t%s\n' "$name" "$(wc -c < "$name" | tr -d ' ')"
done
"""


class RunGeneratedScriptTests(TempDirTestCase):
    """Run the generated script for real, with ssh, rsync and stat shimmed.

    Everything else about build_transfer_script is asserted as text. This is
    the one test that executes it, which is the only way to know that the
    two-layer quoting actually survives a shell -- the script's own parse and
    then the "remote" shell's parse of the command string it passes through.
    A quoting mistake there is invisible to a substring assertion and fatal in
    production.
    """

    def setUp(self):
        super(RunGeneratedScriptTests, self).setUp()
        self.bin = os.path.join(self.root, "bin")
        self.staging = os.path.join(self.root, "staging")
        self.dest_root = os.path.join(self.root, "cluster")
        for path in (self.bin, self.staging, self.dest_root):
            os.makedirs(path)
        self.trace = os.path.join(self.root, "trace")
        for name, body in (
            ("ssh", SSH_SHIM), ("rsync", RSYNC_SHIM), ("stat", STAT_SHIM)
        ):
            shim = os.path.join(self.bin, name)
            with open(shim, "w") as handle:
                handle.write(body)
            os.chmod(shim, 0o755)

    def stage(self, basenames=("m84_a.hifi_reads.bam", "m84_b.hifi_reads.bam"),
              leaf="r84_run", **script_kwargs):
        """Write the same three files the action writes, then the script."""
        files = []
        for index, basename in enumerate(basenames):
            path = self.make_file(basename, b"X" * (100 * (index + 1)))
            files.append(
                {
                    "path": path,
                    "basename": basename,
                    "size": os.stat(path).st_size,
                    "mtime": 0,
                }
            )
        with open(os.path.join(self.staging, "manifest.txt"), "w") as handle:
            for entry in files:
                handle.write("%s\n" % entry["path"])
        with open(os.path.join(self.staging, "expected.tsv"), "w") as handle:
            for entry in files:
                handle.write("%s\t%d\n" % (entry["basename"], entry["size"]))

        dest_path = os.path.join(self.dest_root, leaf)
        os.makedirs(dest_path)
        script_path = os.path.join(self.staging, "transfer.sh")
        with open(script_path, "w") as handle:
            handle.write(
                transfer.build_transfer_script(
                    self.staging, files, DESTINATION, dest_path, **script_kwargs
                )
            )
        self.dest_path = dest_path
        self.files = files
        return script_path

    def execute(self, script_path, **env_overrides):
        import subprocess
        env = dict(os.environ)
        env.update(
            {
                "PATH": self.bin + os.pathsep + env.get("PATH", ""),
                "SHIM_TRACE": self.trace,
                "SHIM_BIN": self.bin,
            }
        )
        env.update({k: v for k, v in env_overrides.items() if v is not None})
        completed = subprocess.run(
            ["bash", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=self.staging,
        )
        return completed.returncode, completed.stdout.decode("utf-8", "replace")

    def landed(self, basename):
        return os.path.join(self.dest_path, basename)

    def sentinel(self, name):
        return os.path.join(self.dest_path, name)

    def test_a_good_transfer_verifies_and_writes_the_sentinel(self):
        returncode, output = self.execute(self.stage())
        self.assertEqual(returncode, 0, output)
        for entry in self.files:
            self.assertEqual(
                os.stat(self.landed(entry["basename"])).st_size, entry["size"]
            )
        self.assertTrue(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))
        self.assertFalse(os.path.exists(self.sentinel(transfer.SENTINEL_FAILED)))

    def test_a_failed_rsync_records_its_exit_status_and_no_sentinel(self):
        returncode, output = self.execute(self.stage(), SHIM_RSYNC_RC="23")
        self.assertEqual(returncode, 23, output)
        self.assertFalse(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))
        with open(self.sentinel(transfer.SENTINEL_FAILED)) as handle:
            self.assertIn("rsync exit 23", handle.read())

    def test_a_truncated_file_is_caught_by_the_size_check(self):
        # The failure mode the size check exists for: rsync exits 0, the bytes
        # are not all there. Without the check, the sentinel would be written
        # and a pipeline would run on a truncated BAM.
        script = self.stage()
        returncode, output = self.execute(
            script, SHIM_TRUNCATE="m84_a.hifi_reads.bam"
        )
        self.assertEqual(returncode, 20, output)
        self.assertFalse(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))
        with open(self.sentinel(transfer.SENTINEL_FAILED)) as handle:
            self.assertIn("size mismatch", handle.read())

    def test_a_dry_run_transfers_nothing_and_claims_nothing(self):
        returncode, output = self.execute(self.stage(dry_run=True))
        self.assertEqual(returncode, 0, output)
        for entry in self.files:
            self.assertFalse(os.path.exists(self.landed(entry["basename"])))
        self.assertFalse(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))
        self.assertFalse(os.path.exists(self.sentinel(transfer.SENTINEL_FAILED)))

    def test_it_survives_a_space_in_a_basename(self):
        returncode, output = self.execute(
            self.stage(basenames=("m84 copy.hifi_reads.bam",))
        )
        self.assertEqual(returncode, 0, output)
        self.assertTrue(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))

    def test_it_survives_shell_metacharacters_in_the_destination_path(self):
        # The end-to-end version of the injection tests: a leaf directory
        # named like a command, executed through two real shells. If the
        # quoting were wrong, this would either fail or -- worse -- run the
        # payload.
        canary = os.path.join(self.root, "canary")
        returncode, output = self.execute(
            self.stage(leaf="r84; touch %s" % canary)
        )
        self.assertEqual(returncode, 0, output)
        self.assertFalse(
            os.path.exists(canary), "the injected command was executed"
        )
        self.assertTrue(os.path.exists(self.sentinel(transfer.SENTINEL_COMPLETE)))
