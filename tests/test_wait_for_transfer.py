"""Tests for actions/wait_for_transfer.py.

Two things are faked, both for the same reason -- otherwise these tests would
take eight hours:

- the clock, so a timeout is reached in microseconds and every assertion about
  elapsed time and poll cadence is exact rather than approximate;
- ssh, because what is under test is how this action reacts to answers, not
  how it asks.

The distinction that matters throughout: an ssh that cannot answer is NOT the
same as "not finished yet". A brief network problem must not abort a healthy
six-hour transfer, and an unreadable answer must not read as "still going"
for the rest of the timeout.

    python3 -m unittest tests.test_wait_for_transfer -v
"""

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


wt = _load("wait_for_transfer", "actions/wait_for_transfer.py")


class FakeClock(object):
    """A clock that only moves when the action sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeCompleted(object):
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeSubprocess(object):
    """Answers each poll from a scripted list, then repeats the last answer."""

    PIPE = real_subprocess.PIPE
    TimeoutExpired = real_subprocess.TimeoutExpired

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        answer = (
            self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        )
        if isinstance(answer, Exception):
            raise answer
        return answer


def complete():
    return FakeCompleted(0, b"COMPLETE\n")


def pending():
    return FakeCompleted(0, b"PENDING\n")


def failed(reason=b"rsync exit 23"):
    return FakeCompleted(0, b"FAILED\n" + reason + b"\n")


def ssh_broken():
    return FakeCompleted(255, b"", b"ssh: connect to host port 22: No route to host")


class WaitTestCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="wait_test_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.log_path = os.path.join(self.root, "transfer.log")
        with open(self.log_path, "w") as handle:
            handle.write("\n".join("log line %d" % i for i in range(100)) + "\n")

        self.clock = FakeClock()
        self.original_time = wt.time
        wt.time = self.clock
        self.original_subprocess = wt.subprocess
        self.addCleanup(self._restore)

    def _restore(self):
        wt.time = self.original_time
        wt.subprocess = self.original_subprocess

    def config(self):
        return {
            "transfer": {
                "default_destination": "marvin",
                "destinations": {
                    "marvin": {
                        "host": "marvin.example.se",
                        "user": "stanley",
                        "ssh_key_path": "/home/stanley/.ssh/id_rsa",
                        "dest_root": "/scratch/pacbio/runs",
                    }
                },
            }
        }

    def wait(self, answers, **kwargs):
        wt.subprocess = FakeSubprocess(answers)
        self.fake = wt.subprocess
        params = {
            "dest_path": "/scratch/pacbio/runs/r84189_20260908",
            "timeout_sec": 600,
            "poll_interval_sec": 60,
            "log_path": self.log_path,
        }
        params.update(kwargs)
        return wt.WaitForTransfer(config=self.config()).run(**params)


class SuccessTests(WaitTestCase):
    def test_a_finished_transfer_returns_immediately(self):
        success, result = self.wait([complete()])
        self.assertTrue(success)
        self.assertEqual(result["polls"], 1)
        self.assertEqual(result["elapsed_sec"], 0)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(result["dest_path"], "/scratch/pacbio/runs/r84189_20260908")
        self.assertEqual(result["destination"], "marvin")

    def test_it_waits_until_the_sentinel_appears(self):
        success, result = self.wait([pending(), pending(), complete()])
        self.assertTrue(success)
        self.assertEqual(result["polls"], 3)
        self.assertEqual(self.clock.sleeps, [60, 60])
        self.assertEqual(result["elapsed_sec"], 120)

    def test_the_poll_interval_is_honoured(self):
        self.wait([pending(), complete()], poll_interval_sec=15)
        self.assertEqual(self.clock.sleeps, [15])

    def test_a_transfer_already_done_when_the_timeout_is_zero_still_succeeds(self):
        # The state is always read at least once. A zero or already-expired
        # timeout must not report failure without having looked.
        success, _ = self.wait([complete()], timeout_sec=0)
        self.assertTrue(success)


class FailureTests(WaitTestCase):
    def test_a_failed_transfer_fails_fast(self):
        success, result = self.wait([failed(), complete()])
        self.assertFalse(success)
        self.assertEqual(result["error"], "transfer_failed")
        # Reported from the destination's own .transfer_failed, so the reason
        # is the far side's, not a guess.
        self.assertIn("rsync exit 23", result["reason"])
        # One poll: it does not wait out the timeout on a known failure, and
        # does not poll again after deciding.
        self.assertEqual(result["polls"], 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_failure_reports_the_tail_of_the_local_log(self):
        _, result = self.wait([failed()])
        self.assertIn("log line 99", result["log_tail"])
        self.assertNotIn("log line 50", result["log_tail"])

    def test_a_timeout_reports_elapsed_time_and_the_log(self):
        success, result = self.wait([pending()], timeout_sec=180)
        self.assertFalse(success)
        self.assertEqual(result["error"], "timeout")
        self.assertGreaterEqual(result["elapsed_sec"], 180)
        self.assertIn("log line 99", result["log_tail"])

    def test_a_timeout_does_not_overshoot_by_a_whole_interval(self):
        # Sleeping a full interval past the deadline turns an 8h timeout into
        # 8h1m for no reason, and makes the reported elapsed time a lie.
        _, result = self.wait([pending()], timeout_sec=100, poll_interval_sec=60)
        self.assertEqual(result["elapsed_sec"], 100)
        self.assertEqual(self.clock.sleeps, [60, 40])


class TransientFailureTests(WaitTestCase):
    def test_a_brief_outage_does_not_abort_the_wait(self):
        # The whole point: a transfer that is fine must not be failed because
        # the login node blinked.
        success, result = self.wait(
            [ssh_broken(), ssh_broken(), pending(), complete()]
        )
        self.assertTrue(success)
        self.assertEqual(result["polls"], 4)
        self.assertEqual(result["ssh_failures"], 2)

    def test_it_gives_up_after_too_many_consecutive_failures(self):
        success, result = self.wait([ssh_broken()], max_ssh_failures=3)
        self.assertFalse(success)
        self.assertEqual(result["error"], "ssh_unreachable")
        self.assertEqual(result["polls"], 3)
        self.assertIn("No route to host", result["reason"])

    def test_the_failure_count_resets_after_a_good_answer(self):
        """Consecutive, not cumulative.

        Four failed polls with max_ssh_failures=3: succeeding at all is the
        assertion. Counting cumulatively, this wait would have given up on the
        fourth -- which is how an eight-hour wait across a flaky link
        accumulates its way to a spurious failure.

        ssh_failures reports the total for diagnosis, which is why it is 4
        here and not the (reset) consecutive count.
        """
        answers = [ssh_broken(), ssh_broken(), pending(),
                   ssh_broken(), ssh_broken(), complete()]
        success, result = self.wait(answers, max_ssh_failures=3)
        self.assertTrue(success)
        self.assertEqual(result["ssh_failures"], 4)

    def test_an_ssh_that_hangs_is_treated_as_a_failed_poll(self):
        success, result = self.wait(
            [real_subprocess.TimeoutExpired(["ssh"], 60), complete()]
        )
        self.assertTrue(success)
        self.assertEqual(result["ssh_failures"], 1)

    def test_unreadable_output_is_retried_not_believed(self):
        # A login banner is not "pending". Believing it would wait out the
        # entire timeout on a transfer that had already finished.
        success, result = self.wait(
            [FakeCompleted(0, b"Welcome to Marvin\n"), complete()]
        )
        self.assertTrue(success)
        self.assertEqual(result["ssh_failures"], 1)

    def test_persistent_unreadable_output_eventually_fails(self):
        success, result = self.wait(
            [FakeCompleted(0, b"Welcome to Marvin\n")], max_ssh_failures=2
        )
        self.assertFalse(success)
        self.assertEqual(result["error"], "ssh_unreachable")


class MiscTests(WaitTestCase):
    def test_no_log_path_is_not_an_error(self):
        _, result = self.wait([failed()], log_path=None)
        self.assertEqual(result["log_tail"], "")

    def test_it_polls_the_destination_it_was_given(self):
        self.wait([complete()], destination="marvin")
        command = self.fake.calls[0][-1]
        self.assertIn("/scratch/pacbio/runs/r84189_20260908/.transfer_complete", command)
        self.assertIn("stanley@marvin.example.se", self.fake.calls[0])

    def test_an_unknown_destination_fails_before_polling(self):
        from lib import transfer
        with self.assertRaises(transfer.TransferError):
            self.wait([complete()], destination="rackham")
        self.assertEqual(self.fake.calls, [])


class DerivedPathTests(WaitTestCase):
    """Being able to work out where to look, instead of being told.

    A workflow that carries dest_path and log_path from the transfer task into
    the wait task couples the two through the plumbing. Deriving both from the
    same config and run name that transfer_bams derived them from means the
    two agree by construction -- and it makes this action runnable by hand
    against a run that is already in flight, which is what an operator
    actually wants at 2am.
    """

    def test_dest_path_is_derived_from_the_run_name(self):
        success, result = self.wait(
            [complete()], dest_path=None, run_name="r84189_20260908"
        )
        self.assertTrue(success)
        self.assertEqual(result["dest_path"], "/scratch/pacbio/runs/r84189_20260908")
        self.assertIn(
            "/scratch/pacbio/runs/r84189_20260908/.transfer_complete",
            self.fake.calls[0][-1],
        )

    def test_dest_subdir_is_honoured(self):
        _, result = self.wait(
            [complete()], dest_path=None, run_name="r84189_20260908",
            dest_subdir="rerun_2",
        )
        self.assertEqual(result["dest_path"], "/scratch/pacbio/runs/rerun_2")

    def test_the_log_is_found_without_being_named(self):
        _, result = self.wait(
            [failed()], dest_path=None, run_name="r84189_20260908", log_path=None
        )
        # Derived, not guessed: the same staging path transfer_bams writes to.
        self.assertEqual(result["error"], "transfer_failed")
        self.assertIn("log_tail", result)

    def test_an_explicit_dest_path_wins(self):
        _, result = self.wait(
            [complete()], dest_path="/scratch/elsewhere/r84", run_name="r84189"
        )
        self.assertEqual(result["dest_path"], "/scratch/elsewhere/r84")

    def test_an_explicit_log_path_wins(self):
        _, result = self.wait(
            [failed()], dest_path=None, run_name="r84189_20260908",
            log_path=self.log_path,
        )
        self.assertIn("log line 99", result["log_tail"])

    def test_neither_dest_path_nor_run_name_is_an_error(self):
        from lib import transfer
        with self.assertRaises(transfer.TransferError) as ctx:
            self.wait([complete()], dest_path=None)
        self.assertIn("run_name", str(ctx.exception))

    def test_a_run_name_that_is_not_a_usable_directory_name_fails(self):
        from lib import transfer
        with self.assertRaises(transfer.TransferError):
            self.wait([complete()], dest_path=None, run_name="r84; rm -rf /")
