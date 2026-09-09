"""Pure helpers for the SMRT Link storage -> HPC BAM transfer.

Deliberately free of st2 imports so it can be unit-tested without an ST2
install; actions/transfer_bams.py and actions/wait_for_transfer.py are thin
wrappers over this. Nothing here touches the network -- the functions either
look at the local filesystem (the st2 host mounts the sequencing storage) or
build argv lists and a shell script for something else to run.
"""

import glob as globmodule
import os
import re
import shlex


class TransferError(Exception):
    """A transfer could not be started, or could not be described."""


DEFAULT_BWLIMIT = "200M"
# Two patterns, not one. "*.hifi_reads.bam" alone matches an undemultiplexed
# collection and NOTHING on the demux path: fnmatch requires the name to end
# with .hifi_reads.bam, while a SMRT Link child dataset's BAM ends with
# .bcM####.bam (or .bc1015--bc1015.bam, or .unassigned.bam). Since the demux
# path is the LongPlex path this pipeline exists for, the one-pattern default
# would have failed with "no files matched" at transfer time on exactly the
# runs that matter.
#
# The second pattern is deliberately broader than an enumeration of the
# infixes we know about, because which ones a given SMRT Link version emits
# has not been confirmed against a real demultiplexed run. One BAM too many
# costs bytes; one BAM too few means a pool silently never arrives. Both
# patterns still exclude .pbi sidecars, subreads and .consensusreadset.xml,
# and the basename-collision and zero-byte checks apply regardless.
DEFAULT_FILE_PATTERNS = ["*.hifi_reads.bam", "*.hifi_reads.*.bam"]
REQUIRED_DESTINATION_KEYS = ("host", "user", "ssh_key_path", "dest_root")
SUPPORTED_TRANSFER_METHODS = ("rsync",)
SUPPORTED_VERIFY_MODES = ("size", "checksum")

# --bwlimit is the one value interpolated bare into the rsync command line, so
# its shape is checked instead of quoted. rsync accepts a number with an
# optional unit suffix, KiB by default.
BWLIMIT_RE = re.compile(r"^[0-9]+(\.[0-9]+)?([KkMmGgTtPp](i?[Bb])?)?$")

# The leaf directory name on the destination. Deliberately narrow: it is a
# directory people and later actions have to name, so anything needing shell
# quoting is rejected rather than quoted and kept.
DEST_SUBDIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Far-side rsync timeout, paired with the ssh keepalives above: both are for a
# long transfer whose control channel crosses a stateful firewall.
RSYNC_IO_TIMEOUT = 600

# One short round trip to the destination -- asking a question, never
# transferring. Bounded so a wedged ssh fails a poll instead of hanging the
# action that owns it.
SSH_CALL_TIMEOUT = 60

# How much of the local transfer log is reported alongside a failure.
LOG_TAIL_LINES = 40

# Where transfer scripts, logs, pids and locks live on the st2 host when pack
# config does not say. Under /var/lib/st2 rather than /tmp: a lock in a
# tmp-cleaned directory silently stops protecting anything.
DEFAULT_STAGING_DIR = "/var/lib/st2/ductus/transfers"

# Filenames inside the per-run staging directory. Named here rather than in
# the action so the generated script, the action's return value and the tests
# cannot drift apart.
MANIFEST_NAME = "manifest.txt"
EXPECTED_NAME = "expected.tsv"
ACTUAL_NAME = "actual.tsv"
SCRIPT_NAME = "transfer.sh"
LOG_NAME = "transfer.log"
PID_NAME = "transfer.pid"

# Written on the DESTINATION, and the whole contract with anything downstream:
# .transfer_complete appears only after the far-side sizes have been checked,
# so its presence means every BAM is fully there.
SENTINEL_COMPLETE = ".transfer_complete"
SENTINEL_FAILED = ".transfer_failed"

# BatchMode: an unattended action must fail rather than block on a passphrase
# or a host-key prompt (a passphrase-protected key needs ssh-agent on the st2
# host; nothing here will prompt). The keepalives are for the control channel
# of a multi-hour transfer crossing a stateful firewall.
SSH_BASE_OPTS = (
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=6",
)


def resolve_source_files(source_paths, file_patterns):
    """The BAMs named by a caller's source_paths, deduplicated and sorted.

    A source_path that is a file is taken as given: an explicit path is a
    decision the caller already made, and filtering it through file_patterns
    would silently drop a BAM someone named by hand. A source_path that is a
    directory is globbed, one pattern at a time, at its own level and one
    level below it -- Revio writes <run>/<collection>/<movie>.hifi_reads.bam,
    so the collection directories have to be reached, but an unbounded walk
    of a run directory also picks up demux scratch and previous-analysis
    copies. Two levels is the bound.

    Deduplication is by realpath, not by the string given, because the same
    BAM legitimately arrives twice -- once from a collection directory and
    once as an explicit path or a convenience symlink -- and rsync would
    otherwise be asked to place two sources at one destination name.

    Sorted, so the manifest order (and therefore the rsync order and the
    expected.tsv order) does not depend on readdir order.
    """
    if not file_patterns:
        raise TransferError(
            "no file_patterns to collect: refusing to guess which files in "
            "%s are the ones to transfer" % ", ".join(source_paths)
        )

    resolved = set()
    searched = []
    for entry in source_paths:
        path = os.path.abspath(entry)
        if os.path.isfile(path):
            resolved.add(os.path.realpath(path))
        elif os.path.isdir(path):
            searched.append(path)
            for pattern in file_patterns:
                for depth in (pattern, os.path.join("*", pattern)):
                    for candidate in globmodule.glob(os.path.join(path, depth)):
                        if os.path.isfile(candidate):
                            resolved.add(os.path.realpath(candidate))
        else:
            # Most likely cause is the sequencing storage not being mounted on
            # the st2 host. That has to be a loud error here rather than an
            # empty file list, which would otherwise look like a finished
            # transfer of nothing.
            raise TransferError(
                "source path %s is neither a file nor a directory on this "
                "host; if it came from collectionPathUri, check that the "
                "sequencing storage is mounted" % path
            )

    if not resolved:
        raise TransferError(
            "no files matched %s under %s"
            % (", ".join(file_patterns), ", ".join(searched) or "the given paths")
        )
    return sorted(resolved)


def validate_files(paths):
    """Describe every file to be transferred, or refuse to start.

    Returns one dict per file with path, basename, size and mtime; the sizes
    are what the far side is checked against after rsync exits, so they are
    read once, here, before anything is copied.

    Every check is a refusal rather than a warning, because each one
    corresponds to a way the transfer would otherwise "succeed" while being
    wrong: a zero-byte BAM looks transferred, a colliding basename lands one
    file on top of the other (the destination is flat -- see --no-relative in
    build_transfer_script), and a newline or tab in a path corrupts the
    newline-delimited manifest or the tab-delimited size list into describing
    something other than what was validated.
    """
    if not paths:
        raise TransferError("no files to transfer")

    described = []
    by_basename = {}
    for path in paths:
        if "\n" in path or "\t" in path:
            raise TransferError(
                "path %r contains a newline or tab; rsync --files-from is "
                "newline-delimited and the size manifest is tab-delimited, so "
                "this file cannot be transferred safely" % path
            )
        if not os.path.isfile(path):
            raise TransferError("%s is missing or is not a regular file" % path)
        if not os.access(path, os.R_OK):
            raise TransferError("%s is not readable by this user" % path)
        size = os.stat(path).st_size
        if size == 0:
            raise TransferError(
                "%s is zero bytes; refusing to transfer an empty BAM" % path
            )
        basename = os.path.basename(path)
        if basename in by_basename:
            raise TransferError(
                "two source files share the basename %s (%s and %s); the "
                "destination directory is flat, so one would overwrite the "
                "other" % (basename, by_basename[basename], path)
            )
        by_basename[basename] = path
        described.append(
            {
                "path": path,
                "basename": basename,
                "size": size,
                "mtime": int(os.stat(path).st_mtime),
            }
        )
    return described


def resolve_destination(transfer_config, name=None):
    """One cluster's settings, with defaults applied, or an error.

    Everything cluster-shaped lives in pack config under
    transfer.destinations.<name>, which is what makes "switch to miarka" a
    config change and not a code change. Resolution fails loudly here, at
    launch time, rather than producing an rsync command line that dies
    somewhere in a detached script half an hour later.
    """
    destinations = (transfer_config or {}).get("destinations") or {}
    if not destinations:
        raise TransferError(
            "no transfer destinations are configured; "
            "set transfer.destinations in the pack config"
        )

    chosen = name or (transfer_config or {}).get("default_destination")
    if not chosen:
        raise TransferError(
            "no destination given and transfer.default_destination is not set"
        )
    if chosen not in destinations:
        raise TransferError(
            "unknown transfer destination %r; configured destinations are %s"
            % (chosen, ", ".join(sorted(destinations)))
        )

    configured = destinations[chosen]
    missing = [key for key in REQUIRED_DESTINATION_KEYS if not configured.get(key)]
    if missing:
        raise TransferError(
            "transfer destination %r is missing required setting(s): %s"
            % (chosen, ", ".join(missing))
        )

    method = configured.get("transfer_method") or "rsync"
    if method not in SUPPORTED_TRANSFER_METHODS:
        raise TransferError(
            "transfer destination %r asks for transfer_method %r, but only %s "
            "is implemented; the key exists so another method can be added "
            "later without changing this action's interface"
            % (chosen, method, " / ".join(SUPPORTED_TRANSFER_METHODS))
        )

    return {
        "name": chosen,
        "host": configured["host"],
        "user": configured["user"],
        "ssh_key_path": configured["ssh_key_path"],
        "dest_root": configured["dest_root"],
        "bwlimit": configured.get("bwlimit") or DEFAULT_BWLIMIT,
        "ssh_extra_opts": list(configured.get("ssh_extra_opts") or []),
        "rsync_extra": list(configured.get("rsync_extra") or []),
        "transfer_method": method,
    }


def build_ssh_command(ssh_key_path, extra_opts=None):
    """The ssh invocation, as argv tokens, with no host in it.

    Used twice: as rsync's -e argument and as the prefix of the far-side
    mkdir / stat / touch calls, so the two cannot disagree about the key or
    the options.

    No token may contain whitespace, because rsync word-splits the string it
    is handed for -e -- a key path with a space in it would arrive at ssh as
    two broken options. That is refused by name here rather than emitted as a
    command line that cannot work.
    """
    tokens = ["ssh", "-i", ssh_key_path]
    tokens.extend(SSH_BASE_OPTS)
    tokens.extend(extra_opts or [])
    for token in tokens:
        if any(character.isspace() for character in token):
            raise TransferError(
                "ssh option %r contains whitespace; rsync splits its -e "
                "argument on spaces, so this cannot be passed through" % token
            )
    return tokens


def build_probe_argv(manifest_path, remote_spec, ssh_command, mode="size"):
    """A read-only comparison of the manifest against the destination.

    Always a dry run: this is what an already-completed transfer is detected
    with, so it must never be the thing that copies anything. Mirrors the real
    transfer's --files-from / --no-relative flattening so it compares the same
    destination names the transfer would write.
    """
    if mode not in SUPPORTED_VERIFY_MODES:
        raise TransferError(
            "unknown comparison mode %r; expected one of %s"
            % (mode, ", ".join(SUPPORTED_VERIFY_MODES))
        )
    return [
        "rsync",
        "-n",
        "-i",
        "-rlpt",
        "--no-relative",
        # As in the real transfer: keep the remote path out of the remote
        # shell.
        "--protect-args",
        "--files-from=%s" % manifest_path,
        "--size-only" if mode == "size" else "--checksum",
        "-e",
        " ".join(ssh_command),
        "/",
        remote_spec,
    ]


def probe_reports_differences(output):
    """True if an itemized dry run says content would still be transferred.

    rsync -i prefixes a line per file it would act on: '<' or '>' for a
    content transfer in either direction and '*' for a message such as
    *deleting. A '.' prefix means the file itself matches and only attributes
    differ -- a permission or timestamp mismatch is not a corrupt BAM, and the
    transfer sets both anyway (--chmod, -t), so treating it as a difference
    would recopy hundreds of gigabytes over metadata. Anything else is rsync's
    own prose ("sending incremental file list", the stats block) and is
    ignored.
    """
    for line in (output or "").splitlines():
        line = line.strip()
        if line and line[0] in "<>*":
            return True
    return False


def validate_dest_subdir(name):
    """The leaf directory name on the destination, or an error.

    Rejected rather than sanitised. Quoting already stops a hostile run_name
    from becoming a command, but this name is also a directory that operators
    and downstream actions have to refer to, so one that needs quoting is one
    somebody should look at -- and a silently rewritten name no longer matches
    the run it came from.
    """
    if not name:
        raise TransferError("no destination subdirectory name given")
    if not DEST_SUBDIR_RE.match(name):
        raise TransferError(
            "%r is not usable as a destination directory name: expected "
            "letters, digits, '.', '_' or '-' only, starting with a letter or "
            "digit. Pass dest_subdir explicitly if the run name is not a "
            "usable directory name." % name
        )
    return name


def remote_target(destination):
    """user@host, as rsync and ssh both want it."""
    return "%s@%s" % (destination["user"], destination["host"])


def remote_spec(destination, dest_path):
    """user@host:/dest/, with the trailing slash rsync needs.

    Without it, rsync transferring a single file treats the destination as a
    filename and the BAM arrives named after the run directory.
    """
    return "%s:%s/" % (remote_target(destination), dest_path.rstrip("/"))


def build_prepare_argv(ssh_command, destination, dest_path, clear_sentinels=True):
    """Create the destination directory and clear our own stale sentinels.

    One round trip for two things that must both hold before rsync starts:

    - The directory exists. Not left to rsync --mkpath, which needs 3.2.3 --
      not guaranteed on either cluster.
    - No previous attempt's .transfer_complete or .transfer_failed is still
      sitting there. This is the one deliberate exception to "never rm
      against the destination", and it is not optional: those two files are
      this action's own control state, and a stale one makes
      wait_for_transfer report a previous attempt's outcome as this
      attempt's. Exactly two paths are named, spelled out in full, with no
      glob and no -r -- no BAM can be reached by it.

    Skipped entirely for a dry run, which must not modify the destination.
    """
    dest = dest_path.rstrip("/")
    command = "mkdir -p %s" % _quote(dest)
    if clear_sentinels:
        command += " && rm -f -- %s %s" % (
            _quote("%s/%s" % (dest, SENTINEL_COMPLETE)),
            _quote("%s/%s" % (dest, SENTINEL_FAILED)),
        )
    return list(ssh_command) + [remote_target(destination), command]


def build_sentinel_check_argv(ssh_command, destination, dest_path,
                              sentinel=SENTINEL_COMPLETE):
    """Ask the destination whether a sentinel is there.

    Read-only, and the exit status is the answer: 0 present, 1 absent,
    anything else (255 from ssh itself) means the question was not answered
    and must not be read as "absent".
    """
    return list(ssh_command) + [
        remote_target(destination),
        "test -f %s" % _quote("%s/%s" % (dest_path.rstrip("/"), sentinel)),
    ]


def build_sentinel_read_argv(ssh_command, destination, dest_path,
                             sentinel=SENTINEL_COMPLETE):
    """Read a sentinel's contents -- what .transfer_failed says went wrong."""
    return list(ssh_command) + [
        remote_target(destination),
        "cat %s" % _quote("%s/%s" % (dest_path.rstrip("/"), sentinel)),
    ]


def plan_paths(transfer_config, destination, leaf):
    """Every path one transfer uses, derived from config and the leaf name.

    Both actions call this rather than passing paths to each other: a
    workflow handing dest_path from one task to the next makes the two agree
    only as long as the plumbing is right, whereas config is the thing they
    have to agree on anyway. It also means ductus.wait_for_transfer can be run
    by hand against a run name.

    Keyed by leaf *and* destination, because the same run may legitimately be
    in flight to two clusters at once; those transfers must not share a lock,
    a manifest or a log.
    """
    staging_root = (transfer_config or {}).get("staging_dir") or DEFAULT_STAGING_DIR
    stem = "%s.%s" % (leaf, destination["name"])
    staging_dir = os.path.join(staging_root, stem)
    dest_path = "%s/%s" % (destination["dest_root"].rstrip("/"), leaf)
    return {
        "dest_path": dest_path,
        "sentinel_path": "%s/%s" % (dest_path, SENTINEL_COMPLETE),
        "staging_root": staging_root,
        "staging_dir": staging_dir,
        "lock_path": os.path.join(staging_root, "%s.lock" % stem),
        "manifest_path": os.path.join(staging_dir, MANIFEST_NAME),
        "expected_path": os.path.join(staging_dir, EXPECTED_NAME),
        "script_path": os.path.join(staging_dir, SCRIPT_NAME),
        "log_path": os.path.join(staging_dir, LOG_NAME),
        "pid_path": os.path.join(staging_dir, PID_NAME),
    }


def build_status_argv(ssh_command, destination, dest_path):
    """Ask, in one round trip, what has become of a transfer.

    Prints COMPLETE, or FAILED followed by whatever .transfer_failed says, or
    PENDING. One connection per poll rather than three: an eight-hour wait at
    a minute apart is hundreds of polls, against a login node that may be
    logging and rate-limiting every one of them.

    Completion is checked first. A rerun clears both sentinels, so the two
    should never coexist -- but if they ever do, the verified copy is the
    truth.

    Read-only by construction: two tests and a cat, no redirection.
    """
    dest = dest_path.rstrip("/")
    complete = _quote("%s/%s" % (dest, SENTINEL_COMPLETE))
    failed = _quote("%s/%s" % (dest, SENTINEL_FAILED))
    command = (
        "if [ -f %s ]; then echo COMPLETE; "
        "elif [ -f %s ]; then echo FAILED; cat %s; "
        "else echo PENDING; fi" % (complete, failed, failed)
    )
    return list(ssh_command) + [remote_target(destination), command]


def parse_status_output(output):
    """(state, reason) from build_status_argv's output.

    Lines before the answer are skipped, because login shells on cluster
    nodes print banners and MOTDs. Output with no answer in it at all raises
    rather than defaulting to "pending": a waiter that reads a banner as
    "still going" waits out its entire timeout on a transfer that already
    finished.
    """
    lines = (output or "").splitlines()
    for index, line in enumerate(lines):
        marker = line.strip()
        if marker == "COMPLETE":
            return ("complete", "")
        if marker == "PENDING":
            return ("pending", "")
        if marker == "FAILED":
            return ("failed", "\n".join(lines[index + 1:]).strip())
    raise TransferError(
        "could not read the transfer status from the destination; got %r"
        % (output or "")
    )


def tail_lines(path, count=40):
    """The last `count` lines of a local log, or "" if there is no log.

    A missing or unreadable log must not turn a reportable timeout into an
    unhandled error -- the log is a diagnostic, not the thing being waited on.
    Decoded leniently: rsync --info=progress2 writes carriage returns and can
    be cut mid-sequence when a transfer is killed.
    """
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except (IOError, OSError):
        return ""
    return "\n".join(lines[-count:])


def build_transfer_script(staging_dir, files, destination, dest_path,
                          dry_run=False, verify="size", bwlimit=None):
    """The detached script that does the copy, verifies it and marks it done.

    Emitted rather than run inline because a 200 GB BAM outlives any sane
    action timeout: transfer_bams writes this, launches it detached and
    returns, and wait_for_transfer watches for the sentinel it ends with.

    Quoting policy, since a shell is the transport here:

    - Every interpolated path is a single quoted bash word (_quote).
    - ssh options go in a bash array, so the far-side calls are immune to
      word splitting and globbing; only rsync's -e argument is joined into a
      string, which is why build_ssh_command refuses a token with whitespace.
    - A far-side command is built as a string with its own paths quoted for
      the remote shell, then quoted again as one local word -- two shells,
      two layers.
    - bwlimit is the one value that cannot be a quoted word, so its shape is
      validated instead.

    Client-side version floor: --info=progress2,stats2 needs rsync 3.1,
    --partial-dir and --chmod are 3.x, and --protect-args is 3.0. That floor
    is on the st2 host, which is the one machine this pack controls; the far
    side needs a protocol-30 rsync for --protect-args.
    """
    if verify not in SUPPORTED_VERIFY_MODES:
        raise TransferError(
            "unknown verify mode %r; expected one of %s"
            % (verify, ", ".join(SUPPORTED_VERIFY_MODES))
        )
    limit = bwlimit or destination["bwlimit"]
    if not BWLIMIT_RE.match(str(limit)):
        raise TransferError(
            "bwlimit %r is not a transfer rate (expected e.g. 200M, 1G, 500)"
            % limit
        )

    dest = dest_path.rstrip("/")
    manifest = os.path.join(staging_dir, MANIFEST_NAME)
    expected = os.path.join(staging_dir, EXPECTED_NAME)
    actual = os.path.join(staging_dir, ACTUAL_NAME)
    ssh_command = build_ssh_command(
        destination["ssh_key_path"], destination["ssh_extra_opts"]
    )
    total_bytes = sum(entry["size"] for entry in files)

    rsync = ["rsync", "-rlptv", "--no-o", "--no-g", "--chmod=Dg+rwx,Fg+r"]
    if dry_run:
        rsync.append("-n")
    rsync.extend(
        [
            "--files-from=%s" % _quote(manifest),
            "--no-relative",
            # --protect-args: send the remote path in the protocol instead of
            # through the remote shell, so a metacharacter in dest_root cannot
            # become a remote command.
            "--protect-args",
            # --partial-dir, not --inplace: a killed transfer must not leave a
            # plausible-looking truncated BAM at the final filename.
            "--partial",
            "--partial-dir=.rsync-partial",
            "--bwlimit=%s" % limit,
            "--timeout=%d" % RSYNC_IO_TIMEOUT,
            "--info=progress2,stats2",
        ]
    )
    rsync.extend(_quote(extra) for extra in destination["rsync_extra"])
    rsync.extend(["-e", '"${SSH[*]}"', "/", '"$TARGET:$DEST/"'])

    lines = [
        "#!/bin/bash",
        "#",
        "# Generated by ductus.transfer_bams -- edits here are lost on the next",
        "# run of the action. Launched detached, with this script's own output",
        "# going to %s beside it." % LOG_NAME,
        "#",
        "# No errexit on purpose: the rsync status is captured and acted on, and",
        "# the failure branch has to run rather than abort the script.",
        "set -uo pipefail",
        "",
        "SSH=(%s)" % " ".join(_quote(token) for token in ssh_command),
        "TARGET=%s" % _quote(remote_target(destination)),
        "DEST=%s" % _quote(dest),
        "",
        'echo "[$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)] %s %d file(s), %d bytes, to '
        '$TARGET:$DEST"' % ("would transfer" if dry_run else "transferring",
                            len(files), total_bytes),
        "",
        "# --files-from with a source root of / plus --no-relative flattens the",
        "# absolute source paths into DEST, which is why transfer_bams refuses a",
        "# basename collision before this script is written.",
        " \\\n      ".join(rsync),
        "rc=$?",
    ]

    if dry_run:
        lines.extend(
            [
                "",
                "# A dry run stops here: nothing landed, so there is nothing to",
                "# verify and no sentinel that would be true.",
                'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dry run finished, rsync '
                'status $rc"',
                "exit $rc",
                "",
            ]
        )
        return "\n".join(lines)

    failed_path = "%s/%s" % (dest, SENTINEL_FAILED)
    complete_path = "%s/%s" % (dest, SENTINEL_COMPLETE)
    basenames = " ".join(_quote(entry["basename"]) for entry in files)

    lines.extend(
        [
            "",
            "if [ $rc -ne 0 ]; then",
            "  # The status is piped in rather than interpolated into the remote",
            "  # command, so nothing has to survive two rounds of expansion.",
            '  printf \'rsync exit %%s\\n\' "$rc" | "${SSH[@]}" "$TARGET" %s'
            % _quote("cat > %s" % _quote(failed_path)),
            '  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] rsync failed, status $rc"',
            "  exit $rc",
            "fi",
            "",
            "# Verification is size-only: it catches truncation, which is the",
            "# realistic failure mode after a clean rsync exit, without the full",
            "# re-read of both copies that a checksum pass costs on a",
            "# multi-hundred-gigabyte BAM.",
            '"${SSH[@]}" "$TARGET" %s > %s'
            % (
                _quote(
                    # '\t' as the two-character escape stat interprets, not a
                    # literal tab: a real tab here is invisible in review and
                    # is exactly the kind of thing an editor or a copy-paste
                    # silently turns into spaces. Either produces a real tab
                    # in stat's output, which is what expected.tsv holds.
                    "cd %s && stat -c %s -- %s"
                    % (_quote(dest), _quote("%n\\t%s"), basenames)
                ),
                _quote(actual),
            ),
            "stat_rc=$?",
            "if [ $stat_rc -ne 0 ] || ! diff <(sort %s) <(sort %s); then"
            % (_quote(expected), _quote(actual)),
            '  printf \'%%s\\n\' "size mismatch" | "${SSH[@]}" "$TARGET" %s'
            % _quote("cat > %s" % _quote(failed_path)),
            '  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] far-side sizes do not match '
            'the manifest"',
            "  exit 20",
            "fi",
        ]
    )

    if verify == "checksum":
        probe = build_probe_argv(
            _quote(manifest),
            '"$TARGET:$DEST/"',
            ['"${SSH[*]}"'],
            mode="checksum",
        )
        lines.extend(
            [
                "",
                "# Opt-in second pass: re-reads both copies end to end, roughly",
                "# doubling wall time. An itemised dry run that wants to transfer",
                "# anything means the content differs.",
                "probe=$(%s)" % " ".join(probe),
                'if printf \'%s\' "$probe" | grep -Eq \'^[<>*]\'; then',
                '  printf \'%%s\\n\' "checksum mismatch" | "${SSH[@]}" "$TARGET" %s'
                % _quote("cat > %s" % _quote(failed_path)),
                '  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] checksums do not match"',
                "  exit 21",
                "fi",
            ]
        )

    lines.extend(
        [
            "",
            "# The sentinel is written last and only here: its presence on the",
            "# destination is the contract that every BAM is fully there.",
            '"${SSH[@]}" "$TARGET" %s' % _quote("touch %s" % _quote(complete_path)),
            "touch_rc=$?",
            "if [ $touch_rc -ne 0 ]; then",
            '  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] transfer verified but the '
            'sentinel could not be written"',
            "  exit 22",
            "fi",
            'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] transfer complete and verified"',
            "",
        ]
    )
    return "\n".join(lines)


def _quote(value):
    """A single interpolated value as one shell word, always quoted.

    shlex.quote leaves a value that happens to need no quoting bare; this
    wraps it anyway, so the generated script reads uniformly and a value that
    later grows a space or a semicolon does not change the shape of the line
    it sits in.
    """
    quoted = shlex.quote(str(value))
    if not quoted.startswith("'"):
        quoted = "'%s'" % quoted
    return quoted
