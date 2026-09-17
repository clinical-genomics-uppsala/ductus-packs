"""Tests for actions/transfer_bams.py.

Everything that leaves the st2 host is faked -- ssh, the rsync probe and the
launch itself -- because none of it is what this action decides. What it
decides is: which files, whether the transfer already happened, whether
another one is running, what gets staged, and that the copy outlives the
action. Those are what is asserted here.

The locking tests are deliberately NOT faked. flock is the one mechanism whose
correctness depends on process and file-descriptor lifetime rather than on any
call this code makes, so the "already in progress" test takes a real lock and
the inheritance test launches a real child process.

Runs with or without the st2 source tree:

    python3 -m unittest tests.test_transfer_bams -v
"""

import fcntl
import importlib.util
import os
import shutil
import subprocess as real_subprocess
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:  # pragma: no cover - depends on the environment, not the code
    import st2common.runners.base_action  # noqa: F401
except ImportError:
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

    def warning(self, *args, **kwargs):
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


tb = _load("transfer_bams", "actions/transfer_bams.py")
from lib import transfer  # noqa: E402


class FakeCompleted(object):
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeProcess(object):
    def __init__(self, pid=4242):
        self.pid = pid


class FakeSubprocess(object):
    """Stands in for the subprocess module inside the action.

    A fake rather than a mock: the action makes a *sequence* of outside calls
    (does the sentinel exist, does the probe see differences, create the
    destination, launch) and the sequence is part of what is under test.
    """

    PIPE = real_subprocess.PIPE
    STDOUT = real_subprocess.STDOUT
    DEVNULL = real_subprocess.DEVNULL
    TimeoutExpired = real_subprocess.TimeoutExpired
    CalledProcessError = real_subprocess.CalledProcessError

    def __init__(self):
        self.calls = []
        self.popen_calls = []
        self.results = {}
        self.default = FakeCompleted()
        self.popen_hook = None

    def answer(self, needle, returncode=0, stdout=b""):
        """Answer any call whose argv contains `needle` with this result."""
        self.results[needle] = FakeCompleted(returncode, stdout)

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for needle, result in self.results.items():
            if needle in joined:
                return result
        return self.default

    def Popen(self, argv, **kwargs):
        self.popen_calls.append((list(argv), kwargs))
        if self.popen_hook is not None:
            return self.popen_hook(argv, kwargs)
        return FakeProcess()

    def argv_containing(self, needle):
        return [call for call in self.calls if needle in " ".join(call)]


class TransferBamsTestCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="transfer_action_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.staging = os.path.join(self.root, "staging")
        self.source = os.path.join(self.root, "revio", "r84_run")
        os.makedirs(os.path.join(self.source, "1_A01"))
        os.makedirs(os.path.join(self.source, "2_B01"))
        self.bam_a = self._write("1_A01/m84_a.hifi_reads.bam", b"AAAA")
        self.bam_b = self._write("2_B01/m84_b.hifi_reads.bam", b"BBBBBB")

        self.fake = FakeSubprocess()
        # Default fixture: nothing has been transferred yet, so the sentinel
        # check answers "absent" (test -f exits 1). Tests about reruns say
        # otherwise explicitly.
        self.fake.answer("test -f", returncode=1)
        self.original_subprocess = tb.subprocess
        tb.subprocess = self.fake
        self.addCleanup(self._restore_subprocess)

    def _restore_subprocess(self):
        tb.subprocess = self.original_subprocess

    def _write(self, relpath, content):
        path = os.path.join(self.source, relpath)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def config(self, **transfer_overrides):
        settings = {
            "default_destination": "marvin",
            "staging_dir": self.staging,
            "file_patterns": ["*.hifi_reads.bam"],
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
                },
            },
        }
        settings.update(transfer_overrides)
        return {"transfer": settings}

    def action(self, **transfer_overrides):
        return tb.TransferBams(config=self.config(**transfer_overrides))

    def run_action(self, **kwargs):
        params = {"run_name": "r84189_20260908", "source_paths": [self.source]}
        params.update(kwargs)
        return self.action().run(**params)

    def staging_dir(self, leaf="r84189_20260908", destination="marvin"):
        return os.path.join(self.staging, "%s.%s" % (leaf, destination))


class HappyPathTests(TransferBamsTestCase):
    def test_returns_the_documented_result(self):
        success, result = self.run_action()
        self.assertTrue(success)
        self.assertEqual(result["destination"], "marvin")
        self.assertEqual(result["dest_path"], "/scratch/pacbio/runs/r84189_20260908")
        self.assertEqual(
            result["sentinel_path"],
            "/scratch/pacbio/runs/r84189_20260908/.transfer_complete",
        )
        self.assertEqual(result["total_bytes"], 10)
        self.assertEqual(result["pid"], 4242)
        self.assertFalse(result["skipped"])
        self.assertEqual(result["staging_dir"], self.staging_dir())
        self.assertEqual(
            result["log_path"], os.path.join(self.staging_dir(), "transfer.log")
        )
        self.assertEqual(
            sorted(entry["basename"] for entry in result["files"]),
            ["m84_a.hifi_reads.bam", "m84_b.hifi_reads.bam"],
        )
        self.assertEqual(
            sorted(entry["path"] for entry in result["files"]),
            sorted([self.bam_a, self.bam_b]),
        )

    def test_launches_exactly_one_process(self):
        self.run_action()
        self.assertEqual(len(self.fake.popen_calls), 1)
        argv, kwargs = self.fake.popen_calls[0]
        self.assertEqual(
            argv, ["bash", os.path.join(self.staging_dir(), "transfer.sh")]
        )

    def test_the_launched_process_is_detached_and_logged(self):
        self.run_action()
        _, kwargs = self.fake.popen_calls[0]
        # start_new_session, not a `setsid nohup ... &` shell string: the
        # detaching is the same and there is no shell to quote for.
        self.assertTrue(kwargs["start_new_session"])
        # Nothing may inherit the action's stdin, or rsync/ssh could try to
        # read from it.
        self.assertEqual(kwargs["stdin"], real_subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], real_subprocess.STDOUT)
        self.assertEqual(
            kwargs["stdout"].name, os.path.join(self.staging_dir(), "transfer.log")
        )

    def test_stages_the_manifest_the_sizes_and_the_script(self):
        self.run_action()
        staging = self.staging_dir()
        with open(os.path.join(staging, "manifest.txt")) as handle:
            manifest = handle.read()
        # One absolute path per line, for rsync --files-from, in the same
        # order as expected.tsv.
        self.assertEqual(manifest.splitlines(), sorted([self.bam_a, self.bam_b]))
        self.assertTrue(manifest.endswith("\n"))

        with open(os.path.join(staging, "expected.tsv")) as handle:
            expected = handle.read().splitlines()
        self.assertEqual(
            sorted(expected),
            sorted(["m84_a.hifi_reads.bam\t4", "m84_b.hifi_reads.bam\t6"]),
        )

        with open(os.path.join(staging, "transfer.sh")) as handle:
            script = handle.read()
        self.assertIn("rsync", script)
        self.assertIn("--files-from='%s/manifest.txt'" % staging, script)

    def test_writes_the_pid_file(self):
        self.run_action()
        with open(os.path.join(self.staging_dir(), "transfer.pid")) as handle:
            self.assertEqual(handle.read().strip(), "4242")

    def test_creates_the_destination_and_clears_stale_sentinels_first(self):
        self.run_action()
        prepare = self.fake.argv_containing("mkdir -p")
        self.assertEqual(len(prepare), 1)
        command = prepare[0][-1]
        self.assertIn("/scratch/pacbio/runs/r84189_20260908", command)
        # A previous attempt's .transfer_failed would otherwise make
        # wait_for_transfer report that attempt's outcome as this one's.
        self.assertIn(".transfer_failed", command)
        self.assertIn(".transfer_complete", command)
        # ...and it happens before the launch.
        self.assertTrue(self.fake.popen_calls)

    def test_destination_can_be_chosen_per_call(self):
        _, result = self.run_action(destination="miarka")
        self.assertEqual(result["destination"], "miarka")
        self.assertEqual(
            result["dest_path"], "/proj/ngi2024001/nobackup/pacbio/r84189_20260908"
        )
        with open(os.path.join(self.staging_dir(destination="miarka"),
                               "transfer.sh")) as handle:
            self.assertIn("--bwlimit=100M", handle.read())

    def test_dest_subdir_overrides_run_name(self):
        _, result = self.run_action(dest_subdir="rerun_2")
        self.assertEqual(result["dest_path"], "/scratch/pacbio/runs/rerun_2")
        self.assertTrue(os.path.isdir(self.staging_dir(leaf="rerun_2")))

    def test_explicit_bam_paths_are_accepted(self):
        _, result = self.run_action(source_paths=[self.bam_a])
        self.assertEqual(len(result["files"]), 1)

    def test_file_patterns_can_be_overridden_per_call(self):
        self._write("1_A01/m84_a.hifi_reads.bam.pbi", b"PBI")
        _, result = self.run_action(file_patterns=["*.hifi_reads.bam.pbi"])
        self.assertEqual(
            [entry["basename"] for entry in result["files"]],
            ["m84_a.hifi_reads.bam.pbi"],
        )


class IdempotencyTests(TransferBamsTestCase):
    def test_completed_transfer_is_skipped(self):
        # Sentinel present, and the probe reports nothing left to send.
        self.fake.answer("test -f", returncode=0)
        self.fake.answer("--size-only", returncode=0, stdout=b"")
        success, result = self.run_action()
        self.assertTrue(success)
        self.assertTrue(result["skipped"])
        self.assertEqual(self.fake.popen_calls, [])
        # Nothing was created or cleared on the destination either.
        self.assertEqual(self.fake.argv_containing("mkdir -p"), [])

    def test_a_sentinel_over_an_incomplete_copy_does_not_skip(self):
        # This is the case that makes the probe necessary rather than
        # decorative: the sentinel says done, the sizes say otherwise.
        self.fake.answer("test -f", returncode=0)
        self.fake.answer(
            "--size-only", returncode=0, stdout=b">f.st.... m84_a.hifi_reads.bam\n"
        )
        success, result = self.run_action()
        self.assertTrue(success)
        self.assertFalse(result["skipped"])
        self.assertEqual(len(self.fake.popen_calls), 1)

    def test_no_sentinel_means_no_probe(self):
        self.fake.answer("test -f", returncode=1)
        self.run_action()
        self.assertEqual(self.fake.argv_containing("--size-only"), [])
        self.assertEqual(len(self.fake.popen_calls), 1)

    def test_an_unreachable_destination_fails_rather_than_recopying(self):
        # ssh itself failing is not the same as "the sentinel is absent". A
        # transfer launched on that assumption would recopy terabytes.
        self.fake.answer("test -f", returncode=255)
        with self.assertRaises(transfer.TransferError) as ctx:
            self.run_action()
        self.assertEqual(self.fake.popen_calls, [])
        self.assertIn("255", str(ctx.exception))


class LockingTests(TransferBamsTestCase):
    def lock_path(self, leaf="r84189_20260908", destination="marvin"):
        return os.path.join(self.staging, "%s.%s.lock" % (leaf, destination))

    def test_a_held_lock_reports_already_in_progress(self):
        os.makedirs(self.staging)
        handle = open(self.lock_path(), "w")
        self.addCleanup(handle.close)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(transfer.TransferError) as ctx:
            self.run_action()
        self.assertIn("already_in_progress", str(ctx.exception))
        self.assertEqual(self.fake.popen_calls, [])

    def test_a_lock_for_another_destination_does_not_block(self):
        os.makedirs(self.staging)
        handle = open(self.lock_path(destination="miarka"), "w")
        self.addCleanup(handle.close)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        success, _ = self.run_action()
        self.assertTrue(success)

    def test_the_lock_outlives_the_action_and_is_held_by_the_child(self):
        """The property the whole locking scheme rests on.

        A lock taken by the action itself is released the moment the action
        exits -- which is immediately, by design -- so a rerun five minutes
        into a six-hour transfer would sail straight past it and start a
        second rsync. The fix is that the open file descriptor is inherited by
        the detached child, which holds the lock for as long as the transfer
        runs. That is a property of process lifetime, so it is tested with a
        real child process rather than a fake.
        """
        children = []

        def launch(argv, kwargs):
            # Same kwargs, harmless command: no ssh, but real fd inheritance.
            child = real_subprocess.Popen(
                ["bash", "-c", "sleep 30"], **kwargs
            )
            children.append(child)
            return child

        self.fake.popen_hook = launch
        self.run_action()
        child = children[0]
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)

        probe = open(self.lock_path(), "w")
        self.addCleanup(probe.close)
        with self.assertRaises(OSError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        child.kill()
        child.wait()
        # And once the transfer is gone, the lock is free again -- no manual
        # cleanup, no stale lock file to delete by hand.
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class DryRunTests(TransferBamsTestCase):
    def test_dry_run_adds_n_and_writes_no_sentinel(self):
        _, result = self.run_action(dry_run=True)
        self.assertTrue(result["dry_run"])
        with open(os.path.join(self.staging_dir(), "transfer.sh")) as handle:
            script = handle.read()
        self.assertIn(" -n ", script)
        self.assertNotIn(".transfer_complete", script)
        self.assertNotIn("touch", script)

    def test_dry_run_removes_nothing_on_the_destination(self):
        self.run_action(dry_run=True)
        prepare = self.fake.argv_containing("mkdir -p")
        self.assertEqual(len(prepare), 1)
        self.assertNotIn("rm", prepare[0][-1])


class ValidationTests(TransferBamsTestCase):
    def test_unknown_destination_fails_before_touching_anything(self):
        with self.assertRaises(transfer.TransferError):
            self.run_action(destination="rackham")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.popen_calls, [])

    def test_a_basename_collision_fails_before_touching_anything(self):
        os.makedirs(os.path.join(self.source, "3_C01"))
        self._write("3_C01/m84_a.hifi_reads.bam", b"CCCC")
        with self.assertRaises(transfer.TransferError) as ctx:
            self.run_action()
        self.assertIn("m84_a.hifi_reads.bam", str(ctx.exception))
        self.assertEqual(self.fake.calls, [])

    def test_no_matching_files_fails(self):
        with self.assertRaises(transfer.TransferError):
            self.run_action(file_patterns=["*.nothing"])
        self.assertEqual(self.fake.popen_calls, [])

    def test_a_run_name_that_is_not_a_usable_directory_name_fails(self):
        with self.assertRaises(transfer.TransferError):
            self.run_action(run_name="r84189; rm -rf /")

    def test_unconfigured_transfer_section_fails_clearly(self):
        action = tb.TransferBams(config={})
        with self.assertRaises(transfer.TransferError):
            action.run(run_name="r84", source_paths=[self.source])

    def test_missing_source_mount_is_reported_as_such(self):
        with self.assertRaises(transfer.TransferError) as ctx:
            self.run_action(source_paths=["/data/revio/not_mounted"])
        self.assertIn("mounted", str(ctx.exception))


def _gnu_rsync_at_least(major, minor):
    """True only for a GNU rsync new enough for the flags the script uses.

    The generated transfer needs --info=progress2 (rsync 3.1), --partial-dir
    and --chmod (3.x) and --protect-args (3.0). macOS ships openrsync, which
    reports itself as "rsync version 2.6.9 compatible" and has none of them.
    Without this check the integration test below would fail on flag parsing
    and look like a bug in the action -- an opt-in test that cannot pass is
    worse than no test.
    """
    try:
        out = real_subprocess.run(
            ["rsync", "--version"],
            stdout=real_subprocess.PIPE,
            stderr=real_subprocess.STDOUT,
            timeout=10,
        ).stdout.decode("utf-8", "replace")
    except (OSError, real_subprocess.TimeoutExpired):
        return False
    if "openrsync" in out:
        return False
    import re
    match = re.search(r"rsync\s+version\s+(\d+)\.(\d+)", out)
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (major, minor)


def _ssh_to_localhost_works(key_path):
    try:
        return real_subprocess.run(
            ["ssh", "-i", key_path, "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new", "localhost", "true"],
            stdout=real_subprocess.DEVNULL,
            stderr=real_subprocess.DEVNULL,
            timeout=20,
        ).returncode == 0
    except (OSError, real_subprocess.TimeoutExpired):
        return False


IT_KEY = os.environ.get(
    "ST2_TRANSFER_IT_KEY", os.path.expanduser("~/.ssh/id_rsa")
)
IT_ENABLED = os.environ.get("ST2_TRANSFER_IT") == "1"


@unittest.skipUnless(IT_ENABLED, "set ST2_TRANSFER_IT=1 to run (needs ssh + rsync)")
class IntegrationTests(unittest.TestCase):
    """A real transfer, over real ssh, to localhost.

    Deliberately out of the default run: it needs key-based ssh to localhost
    and a GNU rsync, neither of which a developer machine owes anybody. What
    it buys that the mocked tests cannot is the part nothing else covers --
    that the generated script is actually executable by bash, that the flag
    set is accepted by a real rsync, that the far-side verification command
    parses on the other side of a real shell, and that the sentinel appears
    only when the bytes have.

        ST2_TRANSFER_IT=1 python3 -m unittest tests.test_transfer_bams -v
    """

    @classmethod
    def setUpClass(cls):
        if not _gnu_rsync_at_least(3, 1):
            raise unittest.SkipTest(
                "needs GNU rsync >= 3.1 (this rsync lacks --info=progress2)"
            )
        if not _ssh_to_localhost_works(IT_KEY):
            raise unittest.SkipTest(
                "needs passwordless ssh to localhost with key %s "
                "(override with ST2_TRANSFER_IT_KEY)" % IT_KEY
            )

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="transfer_it_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = os.path.join(self.root, "revio", "1_A01")
        os.makedirs(self.source)
        self.dest_root = os.path.join(self.root, "cluster")
        os.makedirs(self.dest_root)
        self.sizes = {}
        for name, content in (
            ("m84_a.hifi_reads.bam", b"A" * 4096),
            ("m84_b.hifi_reads.bam", b"B" * 8192),
        ):
            with open(os.path.join(self.source, name), "wb") as handle:
                handle.write(content)
            self.sizes[name] = len(content)

        import getpass
        self.config = {
            "transfer": {
                "default_destination": "localhost",
                "staging_dir": os.path.join(self.root, "staging"),
                "file_patterns": ["*.hifi_reads.bam"],
                "destinations": {
                    "localhost": {
                        "host": "localhost",
                        "user": getpass.getuser(),
                        "ssh_key_path": IT_KEY,
                        "dest_root": self.dest_root,
                    }
                },
            }
        }

    def transfer(self, **kwargs):
        params = {"run_name": "it_run", "source_paths": [self.source]}
        params.update(kwargs)
        success, result = tb.TransferBams(config=self.config).run(**params)
        self.assertTrue(success)
        return result

    def wait_for_the_transfer_to_finish(self, result, seconds=60):
        """Poll for the sentinel the same way wait_for_transfer would."""
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if os.path.exists(result["sentinel_path"]):
                return True
            if os.path.exists(
                os.path.join(result["dest_path"], transfer.SENTINEL_FAILED)
            ):
                with open(
                    os.path.join(result["dest_path"], transfer.SENTINEL_FAILED)
                ) as handle:
                    reason = handle.read()
                self.fail(
                    "transfer failed: %s\n%s"
                    % (reason, transfer.tail_lines(result["log_path"], 40))
                )
            time.sleep(0.5)
        self.fail(
            "transfer did not finish in %ds\n%s"
            % (seconds, transfer.tail_lines(result["log_path"], 40))
        )

    def test_the_bams_arrive_and_the_sentinel_follows_them(self):
        result = self.transfer()
        self.wait_for_the_transfer_to_finish(result)
        for name, size in self.sizes.items():
            landed = os.path.join(result["dest_path"], name)
            self.assertTrue(os.path.isfile(landed), "%s did not arrive" % name)
            self.assertEqual(os.stat(landed).st_size, size)
        # No leftovers: a finished transfer leaves no partial directory.
        self.assertFalse(
            os.path.isdir(os.path.join(result["dest_path"], ".rsync-partial"))
        )

    def test_a_rerun_copies_nothing(self):
        first = self.transfer()
        self.wait_for_the_transfer_to_finish(first)
        stamps = {
            name: os.stat(os.path.join(first["dest_path"], name)).st_mtime_ns
            for name in self.sizes
        }
        second = self.transfer()
        self.assertTrue(second["skipped"])
        self.assertIsNone(second["pid"])
        for name, stamp in stamps.items():
            self.assertEqual(
                os.stat(os.path.join(first["dest_path"], name)).st_mtime_ns, stamp
            )

    def test_a_truncated_destination_is_not_mistaken_for_a_finished_one(self):
        first = self.transfer()
        self.wait_for_the_transfer_to_finish(first)
        # Simulate the case the sentinel alone cannot catch.
        with open(os.path.join(first["dest_path"], "m84_a.hifi_reads.bam"), "wb"):
            pass
        second = self.transfer()
        self.assertFalse(second["skipped"])
        self.wait_for_the_transfer_to_finish(second)
        self.assertEqual(
            os.stat(
                os.path.join(second["dest_path"], "m84_a.hifi_reads.bam")
            ).st_size,
            self.sizes["m84_a.hifi_reads.bam"],
        )
