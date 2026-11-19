#!/usr/bin/env python

"""Launches an rsync-over-SSH transfer of a run's HiFi BAMs to an HPC cluster.

The step that was missing between "SMRT Link run registered" and "BAMs
available on the processing cluster" (docs/pacbio_transfer_open_questions.md):
ductus.reheader_pacbio_bams_* and ductus.build_longplex_inputs_marvin were
both written assuming their input directory already existed on the cluster,
and nothing put it there.

This action *starts* a transfer; it does not perform one. A 200 GB BAM
outlives any sane action timeout, so the copy is generated as a script,
launched detached, and left running after the action returns in seconds.
ductus.wait_for_transfer is the other half: it watches for the
.transfer_complete sentinel this action's script writes on the destination.

Nothing cluster-specific is decided here. Every host, user, key, root path and
bandwidth ceiling comes from pack config under transfer.destinations, so
switching a run from Marvin to Miarka is a parameter, not a code path.

Three properties are worth knowing before changing anything:

- The flock is held by the *launched* script, not by this action. The open
  descriptor is inherited by the detached child, which keeps it for the life
  of the transfer. Locking in the action itself would be useless: it exits
  immediately by design, so a rerun five minutes into a six-hour transfer
  would sail past the lock and start a second rsync.
- A rerun over a finished transfer copies nothing. The sentinel alone is not
  trusted for that -- it is confirmed with an itemised, size-only dry run,
  because a sentinel over a half-copied tree is exactly the case that must
  not be skipped.
- The destination's own .transfer_complete / .transfer_failed are cleared
  before a transfer starts. That is the one deliberate exception to "never rm
  against the destination", and it is not optional: those files are this
  action's control state, and a stale one makes wait_for_transfer report a
  previous attempt's outcome as this attempt's. Exactly two paths are named,
  with no glob -- no BAM is reachable by it.
"""

import fcntl
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.transfer import (  # noqa: E402
    DEFAULT_FILE_PATTERNS,
    SENTINEL_COMPLETE,
    SSH_CALL_TIMEOUT,
    TransferError,
    build_probe_argv,
    build_prepare_argv,
    build_sentinel_check_argv,
    build_ssh_command,
    build_transfer_script,
    plan_paths,
    probe_reports_differences,
    remote_spec,
    resolve_destination,
    resolve_source_files,
    validate_dest_subdir,
    validate_files,
)

from st2common.runners.base_action import Action  # noqa: E402

# The probe compares metadata for every file in the manifest over one ssh
# connection. Longer than SSH_CALL_TIMEOUT (which bounds a single question),
# short enough to stay well inside the action's own timeout.
PROBE_TIMEOUT = 300


class TransferBams(Action):
    def _call(self, argv, timeout):
        """Run one bounded command, returning (returncode, stdout, stderr)."""
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return (
            completed.returncode,
            (completed.stdout or b"").decode("utf-8", "replace"),
            (completed.stderr or b"").decode("utf-8", "replace"),
        )

    def _already_transferred(self, ssh_command, destination, paths):
        """True only if the destination holds a complete, verified copy.

        Two questions, in order, because the second one costs a round trip per
        file's metadata: is the sentinel there, and does an itemised size-only
        dry run agree that there is nothing left to send. A sentinel on its
        own is not enough -- it can survive a destination that was later
        truncated or partly cleaned up.

        An ssh that fails to answer is neither yes nor no, and is raised
        rather than read as "not transferred": treating an unreachable cluster
        as an empty destination is how a rerun recopies terabytes.
        """
        dest_path = paths["dest_path"]
        returncode, _, stderr = self._call(
            build_sentinel_check_argv(ssh_command, destination, dest_path),
            SSH_CALL_TIMEOUT,
        )
        if returncode == 1:
            return False
        if returncode != 0:
            raise TransferError(
                "could not tell whether %s already holds a completed transfer: "
                "ssh exited %d (%s)"
                % (dest_path, returncode, stderr.strip() or "no stderr")
            )

        self.logger.info(
            "%s exists; confirming with a size-only probe before skipping",
            SENTINEL_COMPLETE,
        )
        returncode, stdout, stderr = self._call(
            build_probe_argv(
                paths["manifest_path"],
                remote_spec(destination, dest_path),
                ssh_command,
                mode="size",
            ),
            PROBE_TIMEOUT,
        )
        if returncode != 0:
            raise TransferError(
                "the completion sentinel in %s could not be confirmed: rsync "
                "probe exited %d (%s)"
                % (dest_path, returncode, stderr.strip() or "no stderr")
            )
        if probe_reports_differences(stdout):
            self.logger.warning(
                "%s claims %s is complete, but the sizes differ; transferring "
                "again", SENTINEL_COMPLETE, dest_path,
            )
            return False
        return True

    def _stage(self, paths, files, destination, dry_run, verify, bwlimit):
        """Write the manifest, the expected sizes and the transfer script."""
        staging_dir = paths["staging_dir"]
        if not os.path.isdir(staging_dir):
            os.makedirs(staging_dir)

        with open(paths["manifest_path"], "w") as handle:
            # One absolute path per line: rsync --files-from is
            # newline-delimited, which is why validate_files refuses a path
            # containing a newline.
            for entry in files:
                handle.write("%s\n" % entry["path"])

        with open(paths["expected_path"], "w") as handle:
            # What the far side is checked against once rsync exits. Read from
            # the source before the copy starts, so a source that changes
            # mid-transfer shows up as a mismatch rather than being blessed.
            for entry in files:
                handle.write("%s\t%d\n" % (entry["basename"], entry["size"]))

        script_path = paths["script_path"]
        with open(script_path, "w") as handle:
            handle.write(
                build_transfer_script(
                    staging_dir,
                    files,
                    destination,
                    paths["dest_path"],
                    dry_run=dry_run,
                    verify=verify,
                    bwlimit=bwlimit,
                )
            )
        os.chmod(script_path, 0o750)
        return script_path

    def _launch(self, paths, lock_fd):
        """Start the transfer and let it outlive this action.

        start_new_session detaches it from the action's process group, so it
        survives the action exiting and is not killed with it -- the same
        effect as `setsid nohup ... &` without generating a shell command
        line to quote.

        pass_fds is what makes the lock work: the child inherits the locked
        descriptor and holds the flock until it exits.
        """
        log = open(paths["log_path"], "ab")
        try:
            process = subprocess.Popen(
                ["bash", paths["script_path"]],
                cwd=paths["staging_dir"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(lock_fd,),
            )
        finally:
            log.close()

        with open(paths["pid_path"], "w") as handle:
            handle.write("%d\n" % process.pid)
        return process.pid

    def run(self, run_name, source_paths, destination=None, dest_subdir=None,
            file_patterns=None, dry_run=False, bwlimit=None, verify="size"):
        settings = self.config.get("transfer") or {}
        resolved = resolve_destination(settings, destination)
        patterns = (
            file_patterns
            or settings.get("file_patterns")
            or DEFAULT_FILE_PATTERNS
        )
        # The leaf is the identity of the thing being written, so it is what
        # the staging directory and the lock are keyed on -- not run_name,
        # which dest_subdir may override and which is not necessarily a usable
        # directory name.
        leaf = validate_dest_subdir(dest_subdir or run_name)

        files = validate_files(resolve_source_files(source_paths, patterns))
        total_bytes = sum(entry["size"] for entry in files)
        ssh_command = build_ssh_command(
            resolved["ssh_key_path"], resolved["ssh_extra_opts"]
        )

        # Same derivation ductus.wait_for_transfer uses, so the two agree
        # without the workflow having to carry paths between them.
        paths = plan_paths(settings, resolved, leaf)
        dest_path = paths["dest_path"]
        lock_path = paths["lock_path"]
        if not os.path.isdir(paths["staging_root"]):
            os.makedirs(paths["staging_root"])

        self.logger.info(
            "%d file(s), %d bytes, %s -> %s:%s",
            len(files), total_bytes, ", ".join(source_paths),
            resolved["name"], dest_path,
        )

        # Taken before staging and before the probe, so the whole sequence is
        # atomic against a concurrent run: without that, two executions could
        # both probe an incomplete destination and both decide to transfer.
        lock_handle = open(lock_path, "a+")
        lock_fd = lock_handle.fileno()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            lock_handle.close()
            raise TransferError(
                "already_in_progress: a transfer of %s to %s is already "
                "running (lock %s is held). Not starting a second rsync."
                % (leaf, resolved["name"], lock_path)
            )

        try:
            self._stage(paths, files, resolved, dry_run, verify, bwlimit)

            if self._already_transferred(ssh_command, resolved, paths):
                self.logger.info(
                    "%s already holds a complete, verified copy; skipping",
                    dest_path,
                )
                lock_handle.close()
                return (
                    True,
                    self._result(
                        paths, resolved, files, total_bytes, None, True, dry_run
                    ),
                )

            returncode, _, stderr = self._call(
                build_prepare_argv(
                    ssh_command, resolved, dest_path,
                    clear_sentinels=not dry_run,
                ),
                SSH_CALL_TIMEOUT,
            )
            if returncode != 0:
                raise TransferError(
                    "could not prepare %s on %s: ssh exited %d (%s)"
                    % (dest_path, resolved["name"], returncode,
                       stderr.strip() or "no stderr")
                )

            pid = self._launch(paths, lock_fd)
        except Exception:
            lock_handle.close()
            raise

        # Deliberately released here: the detached child inherited the
        # descriptor and now holds the lock for the life of the transfer.
        lock_handle.close()

        self.logger.info(
            "transfer of %s to %s launched as pid %d, logging to %s",
            leaf, resolved["name"], pid, paths["log_path"],
        )
        return (
            True,
            self._result(paths, resolved, files, total_bytes, pid, False, dry_run),
        )

    def _result(self, paths, destination, files, total_bytes, pid, skipped,
                dry_run):
        return {
            "destination": destination["name"],
            "dest_path": paths["dest_path"],
            "sentinel_path": paths["sentinel_path"],
            "files": files,
            "total_bytes": total_bytes,
            "staging_dir": paths["staging_dir"],
            "log_path": paths["log_path"],
            "pid": pid,
            "skipped": skipped,
            "dry_run": dry_run,
        }
