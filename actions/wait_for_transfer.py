#!/usr/bin/env python

"""Waits for a ductus.transfer_bams transfer to finish on the destination.

The other half of ductus.transfer_bams, which launches a detached rsync and
returns in seconds. This action polls the destination for the
.transfer_complete sentinel that transfer_bams' script writes only after the
far-side sizes have been checked, so a success here means every BAM is fully
there -- which is what makes it safe to gate a downstream pipeline on.

One long-lived action rather than an Orquesta-level retry loop: a
`retry: delay: 60, count: 480` produces 480 execution records to page through
when something goes wrong, and hides the elapsed time and the log tail that
actually explain it. One execution, one poll count, one report.

The distinction this action exists to get right is between "not finished yet"
and "could not tell":

- .transfer_failed present -> fail immediately, with the far side's own reason
  and the tail of the local log. Waiting out an eight-hour timeout on a
  transfer that is known to have failed wastes a working day.
- ssh could not answer -> warn and keep polling. A login node that blinks, a
  brief network problem or a rate-limited connection must not fail an
  otherwise healthy six-hour transfer. Only a run of consecutive failures
  (max_ssh_failures) gives up, and the count resets on any good answer:
  cumulative counting would accumulate its way to a spurious failure across a
  long wait on a flaky link.
- an answer that cannot be parsed -> also "could not tell", not "pending". A
  login banner read as "still going" would wait out the whole timeout on a
  transfer that had already finished.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.transfer import (  # noqa: E402
    LOG_TAIL_LINES,
    SSH_CALL_TIMEOUT,
    TransferError,
    build_ssh_command,
    build_status_argv,
    parse_status_output,
    plan_paths,
    resolve_destination,
    tail_lines,
    validate_dest_subdir,
)

from st2common.runners.base_action import Action  # noqa: E402


class WaitForTransfer(Action):
    def _poll(self, argv):
        """(state, detail) for one poll.

        state is one of complete / failed / pending / unknown; "unknown" means
        the destination did not answer, which is deliberately not "pending".
        """
        try:
            completed = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=SSH_CALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return ("unknown", "ssh did not answer within %ds" % SSH_CALL_TIMEOUT)

        stdout = (completed.stdout or b"").decode("utf-8", "replace")
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        if completed.returncode != 0:
            return (
                "unknown",
                "ssh exited %d (%s)"
                % (completed.returncode, stderr.strip() or "no stderr"),
            )
        try:
            return parse_status_output(stdout)
        except TransferError as error:
            return ("unknown", str(error))

    def run(self, dest_path=None, destination=None, run_name=None,
            dest_subdir=None, timeout_sec=28800, poll_interval_sec=60,
            log_path=None, max_ssh_failures=10):
        settings = self.config.get("transfer") or {}
        resolved = resolve_destination(settings, destination)

        # Either be told where to look, or work it out the same way
        # transfer_bams did. Deriving is preferred in a workflow: it makes the
        # two tasks agree through config rather than through plumbing, and it
        # makes this action runnable by hand against a run name.
        if not dest_path or not log_path:
            if not (dest_path or run_name or dest_subdir):
                raise TransferError(
                    "give either dest_path, or run_name (or dest_subdir) to "
                    "derive it from"
                )
            if run_name or dest_subdir:
                paths = plan_paths(
                    settings, resolved, validate_dest_subdir(dest_subdir or run_name)
                )
                dest_path = dest_path or paths["dest_path"]
                log_path = log_path or paths["log_path"]

        argv = build_status_argv(
            build_ssh_command(
                resolved["ssh_key_path"], resolved["ssh_extra_opts"]
            ),
            resolved,
            dest_path,
        )

        started = time.monotonic()
        polls = 0
        consecutive_failures = 0
        total_failures = 0

        while True:
            polls += 1
            state, detail = self._poll(argv)
            elapsed = int(time.monotonic() - started)

            if state == "complete":
                self.logger.info(
                    "%s is complete after %ds and %d poll(s)",
                    dest_path, elapsed, polls,
                )
                return (
                    True,
                    self._result(
                        resolved, dest_path, elapsed, polls, total_failures,
                    ),
                )

            if state == "failed":
                self.logger.error(
                    "transfer to %s failed: %s", dest_path, detail or "no reason given"
                )
                return (
                    False,
                    self._result(
                        resolved, dest_path, elapsed, polls, total_failures,
                        error="transfer_failed",
                        reason=detail or "no reason recorded on the destination",
                        log_path=log_path,
                    ),
                )

            if state == "pending":
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                total_failures += 1
                self.logger.warning(
                    "could not read the state of %s (%d consecutive): %s",
                    dest_path, consecutive_failures, detail,
                )
                if consecutive_failures >= max_ssh_failures:
                    return (
                        False,
                        self._result(
                            resolved, dest_path, elapsed, polls, total_failures,
                            error="ssh_unreachable",
                            reason="%d consecutive failed polls; last: %s"
                            % (consecutive_failures, detail),
                            log_path=log_path,
                        ),
                    )

            remaining = timeout_sec - elapsed
            if remaining <= 0:
                self.logger.error(
                    "timed out after %ds waiting for %s", elapsed, dest_path
                )
                return (
                    False,
                    self._result(
                        resolved, dest_path, elapsed, polls, total_failures,
                        error="timeout",
                        reason="the transfer did not complete within %ds"
                        % timeout_sec,
                        log_path=log_path,
                    ),
                )
            # Never sleep past the deadline: overshooting turns an 8h timeout
            # into 8h1m and makes the reported elapsed time wrong.
            time.sleep(min(poll_interval_sec, remaining))

    def _result(self, destination, dest_path, elapsed, polls, ssh_failures,
                error=None, reason=None, log_path=None):
        result = {
            "destination": destination["name"],
            "dest_path": dest_path,
            "elapsed_sec": elapsed,
            "polls": polls,
            "ssh_failures": ssh_failures,
        }
        if error:
            result["error"] = error
            result["reason"] = reason or ""
            # The log is on the st2 host, written by the detached script. It
            # is where an rsync error message actually is; the sentinel only
            # carries the exit status.
            result["log_tail"] = tail_lines(log_path, LOG_TAIL_LINES)
        return result
