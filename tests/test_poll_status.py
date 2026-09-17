"""Tests for actions/poll_status.py, specifically the polling deadline.

Before this, check_status() looped `while state in (started, pending) or not
state` with no deadline of its own: max_retries bounds only the unreadable-
state branch, so a job stuck in "pending" polled forever and the st2 python
runner's own default timeout silently became the bound. That is fine for the
short reheader jobs the action was written for and wrong for a LongPlex demux
measured in hours -- docs/deferred_findings.md item 3.

The monotonic clock and the sleep are both faked. A test that really waits is
a test nobody runs, and the thing under test is the arithmetic, not time
passing. One test additionally freezes time.time() at a wrong value to prove
the deadline never consults the wall clock.

Runs with or without the st2 source tree:

    python3 -m unittest tests.test_poll_status -v
"""

import importlib.util
import os
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _NullLogger(object):
    """Also carries the handler poll_status.__init__ reformats."""

    class _Formatter(object):
        _fmt = "%(message)s"

    class _Handler(object):
        def __init__(self):
            self.formatter = _NullLogger._Formatter()

        def setFormatter(self, formatter):
            self.formatter = formatter

    def __init__(self):
        self.handlers = [self._Handler()]

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


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


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO_ROOT, relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ps = _load("poll_status_under_test", "actions/poll_status.py")


class FakeClock(object):
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class FakeResponse(object):
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


# Any test here needs a handful of polls at most. The cap exists so that a
# check_status() which fails to honour its deadline fails *fast* instead of
# spinning: the sleeps are faked and instant, so an unbounded loop would
# otherwise run until the real wall clock caught up -- an hour or three of a
# hung test run rather than a red one. Learned the hard way: reverting
# monotonic() to time() used to hang this suite instead of failing it.
MAX_POLLS = 50


class PollLimitExceeded(AssertionError):
    pass


def _build(states, clock):
    """A PollStatus whose query() walks `states`, holding the last one."""
    action = ps.PollStatus.__new__(ps.PollStatus)
    action.logger = _NullLogger()
    action.queried = []

    remaining = list(states)

    def _query(url, verify_ssl_cert, api_key=None):
        if len(action.queried) >= MAX_POLLS:
            raise PollLimitExceeded(
                "check_status() polled {} times without terminating -- its "
                "deadline is not being enforced".format(MAX_POLLS)
            )
        action.queried.append(url)
        state = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return FakeResponse({"state": state})

    action.query = _query
    return action


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_monotonic = ps.time.monotonic
        self._real_sleep = ps.time.sleep
        ps.time.monotonic = self.clock.monotonic
        ps.time.sleep = self.clock.sleep

    def tearDown(self):
        ps.time.monotonic = self._real_monotonic
        ps.time.sleep = self._real_sleep

    def test_a_lying_wall_clock_does_not_move_the_deadline(self):
        """Why the deadline uses monotonic() and not time().

        An NTP step or a VM resuming from suspend moves time.time() in either
        direction mid-poll. Here it is frozen at an absurd value: if the
        deadline arithmetic consulted it, elapsed would read as 0 forever and
        the poll would run to "done" instead of timing out.

        Terminates either way: the state list ends in "done", and MAX_POLLS
        bounds the loop regardless. A regression fails this, it does not hang
        it -- which was not true before MAX_POLLS existed.
        """
        action = _build(["pending", "pending", "pending", "done"], self.clock)

        real_time = ps.time.time
        ps.time.time = lambda: 0.0
        try:
            ok, result = action.check_status(
                "https://example/status/1",
                sleep=60,
                ignore_result=False,
                verify_ssl_cert=True,
                max_retries=3,
                timeout_sec=5400,
            )
        finally:
            ps.time.time = real_time

        self.assertFalse(ok)
        self.assertTrue(result.get("timed_out"))
        self.assertEqual(result["elapsed_sec"], 5400)

    def test_gives_up_once_the_deadline_passes(self):
        """A job stuck in pending must end, not poll forever."""
        action = _build(["pending"], self.clock)

        ok, result = action.check_status(
            "https://example/status/1",
            sleep=10,  # minutes
            ignore_result=False,
            verify_ssl_cert=True,
            max_retries=3,
            timeout_sec=3600,
        )

        self.assertFalse(ok)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["last_state"], "pending")
        self.assertEqual(result["timeout_sec"], 3600)
        self.assertGreaterEqual(result["elapsed_sec"], 3600)

    def test_never_sleeps_past_the_deadline(self):
        """A 60m interval against a 90m deadline must not overshoot by 30m.

        Otherwise the timeout is reported up to a full interval after it
        actually happened, which is the difference between a 3-day deadline
        and a 3-day-plus-an-hour one.
        """
        action = _build(["pending"], self.clock)

        action.check_status(
            "https://example/status/1",
            sleep=60,
            ignore_result=False,
            verify_ssl_cert=True,
            max_retries=3,
            timeout_sec=5400,  # 90 minutes
        )

        self.assertEqual(sum(self.clock.slept), 5400)
        self.assertTrue(all(s >= 0 for s in self.clock.slept))

    def test_done_before_the_deadline_still_succeeds(self):
        action = _build(["pending", "pending", "done"], self.clock)

        ok, result = action.check_status(
            "https://example/status/1",
            sleep=1,
            ignore_result=False,
            verify_ssl_cert=True,
            max_retries=3,
            timeout_sec=3600,
        )

        self.assertTrue(ok)
        self.assertEqual(result["state"], "done")
        self.assertNotIn("timed_out", result)

    def test_terminal_failure_is_unchanged_by_the_deadline(self):
        action = _build(["error"], self.clock)

        ok, result = action.check_status(
            "https://example/status/1",
            sleep=1,
            ignore_result=False,
            verify_ssl_cert=True,
            max_retries=3,
            timeout_sec=3600,
        )

        self.assertFalse(ok)
        self.assertEqual(result["state"], "error")
        self.assertNotIn("timed_out", result)

    def test_none_polls_forever_preserving_the_old_behaviour(self):
        """timeout_sec=None must not introduce a deadline."""
        action = _build(["pending", "pending", "pending", "done"], self.clock)

        ok, _ = action.check_status(
            "https://example/status/1",
            sleep=600,  # 10 hours an interval; no deadline may cut it short
            ignore_result=False,
            verify_ssl_cert=True,
            max_retries=3,
            timeout_sec=None,
        )

        self.assertTrue(ok)
        self.assertEqual(self.clock.slept, [36000, 36000, 36000])


class RequestTimeoutTests(unittest.TestCase):
    """The deadline is only reachable if no single request can hang."""

    def test_query_passes_a_connect_and_read_timeout(self):
        captured = {}

        class _FakeRequests(object):
            @staticmethod
            def get(url, headers=None, verify=None, timeout=None):
                captured["timeout"] = timeout
                return FakeResponse({"state": "done"})

        action = ps.PollStatus.__new__(ps.PollStatus)
        action.logger = _NullLogger()
        real_requests = ps.requests
        ps.requests = _FakeRequests
        try:
            action.query("https://example/status/1", True)
        finally:
            ps.requests = real_requests

        self.assertEqual(captured["timeout"], ps.PollStatus.REQUEST_TIMEOUT)
        self.assertEqual(len(captured["timeout"]), 2)


if __name__ == "__main__":
    unittest.main()
