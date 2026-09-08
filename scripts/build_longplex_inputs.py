#!/usr/bin/env python3
"""Generates the two CSV inputs for the seqWell LongPlex Nextflow pipeline.

    pool_sheet.csv   pool_ID,pool_path,i7_barcode,i5_barcode      (required)
    rename_map.csv   pool_ID.well_ID,sample_ID                    (optional)

Deployment target: the processing cluster (Marvin or Miarka), NOT the st2
host. Invoked the same way scripts/reheader_pacbio_bams.sh is -- named CLI
flags over SSH via core.remote, or through the Miarka processing-service
gateway. It is deliberately self-contained and stdlib-only so that deploying
it is copying one file, with no pack lib/ on the cluster's PYTHONPATH.

Why the work is split across a host boundary at all: resolving which pools a
run has requires the SMRT Link REST API, whose credentials are encrypted st2
datastore keys and must not be shipped to a cluster. Validating that the pool
BAMs actually landed, and writing the CSVs beside them, requires the cluster
filesystem, which the st2 host does not mount. So:

    ductus.resolve_longplex_pools   (on st2, network, no filesystem)
        -> pool_manifest.json
    this script                     (on the cluster, filesystem, no network)
        -> pool_sheet.csv [+ rename_map.csv]

That is also the seam that keeps this half unit-testable against fixtures:
there is no network code here to mock.

Sample-map format
-----------------
CONFIRMED (2026-09-08) as the format in use, e.g. demux_reheader/sample_map.csv:

    Project,Run_nr,Sample_ID,Index_ID
    proj007,Run2,CU01,B01

`Index_ID` carries the LongPlex *inner* well (the seqWell plate well), which
is what `rename_map` keys on. Headers are matched case-insensitively with
surrounding whitespace tolerated, and .xlsx is accepted as well as .csv.

ASSUMPTION, stated rather than confirmed: which pool a sheet row belongs to.
The sheet has no pool column, and it cannot be joined on well -- SMRT Link's
`well` is the Revio plate well of the whole *collection*, while `Index_ID` is
the seqWell well inside the pool. Same A01-H12 shape, different namespaces.
So:

  - one pool in the run  -> every row belongs to it. Unambiguous.
  - a pool column present (pool / pool_ID / barcode) -> use it. Supported now
    so that the day the sheet gains one, this works untouched.
  - several pools, no pool column -> hard error naming the pools. Guessing
    here is the one failure the LongPlex pipeline will not report: a wrong
    pool prefix makes its per-pool filter drop that pool's entire rename set
    silently, and you find out when every output is named bc1015.A01.
"""

import argparse
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

# From the LongPlex pipeline's own schemas/input_schema.json, which gives
# pool_ID as "^[a-zA-Z0-9]*$". Note their `*`: an empty pool_ID passes their
# schema and then silently produces output named ".A01". Require at least one
# character here.
POOL_ID_RE = re.compile(r"^[A-Za-z0-9]+$")

# Rows A-H, columns 01-12, zero-padded, per the pipeline README: "A1 is
# invalid, use A01". Not normalised -- a well outside this set is rejected
# and named, rather than guessed at.
WELL_RE = re.compile(r"^[A-H](0[1-9]|1[0-2])$")

# sample_ID becomes an output filename, so it must not carry a path
# separator or shell/glob metacharacter. Underscores ARE allowed (unlike
# pool_ID) -- the README gives bc1015_sample1 as valid.
SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

BARCODE_SETS = ("set1", "set2", "set3")

# Verified against the checked-out pipeline (LongPlex v3.1): barcodes/ is
# FLAT -- all six .fa files sit in one directory, there are no set1/ set3/
# subdirectories, and the extension is .fa not .fasta. set2 ships too, even
# though the README only explains set1 (post-launch) and set3 (early access).
BARCODE_FASTA_TEMPLATE = "LongPlex_{barcode_set}_i{index}_trimmed_adapters.fa"

POOL_SHEET_COLUMNS = ("pool_ID", "pool_path", "i7_barcode", "i5_barcode")
RENAME_MAP_COLUMNS = ("pool_ID.well_ID", "sample_ID")

# Header aliases, lowercased. Kept as a small table so that adapting to a
# different real sheet is a mapping change, not a parser change.
# Deliberately NOT including a bare "barcode" as a pool alias: on a PacBio
# sheet a column called Barcode could just as plausibly mean the SMRTbell
# (outer) barcode or the seqWell (inner) index, and binding the wrong one
# produces a syntactically valid rename_map carrying the wrong sample names.
# Two headers matching the same role is an error, not a first-wins race --
# see _map_headers.
POOL_HEADERS = ("pool", "pool_id", "pool id", "pool_barcode", "pool barcode")
WELL_HEADERS = ("index_id", "index id", "indexid", "well", "well_id", "well id")
SAMPLE_HEADERS = ("sample_id", "sample id", "sampleid", "sample", "sample_name")

XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class LongPlexInputError(Exception):
    """Input could not be turned into valid pipeline CSVs.

    Carries every problem found, not just the first: a bad sample sheet
    usually has more than one bad row, and one round trip per typo is
    miserable. Raised distinctly from API and transfer failures so an
    Orquesta workflow can branch on validation separately.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(
            "%d input problem(s):\n  - %s"
            % (len(self.errors), "\n  - ".join(self.errors))
        )


# ----------------------------------------------------------------------
# Sample map parsing
# ----------------------------------------------------------------------


def _clean(value):
    if value is None:
        return ""
    # Strip a UTF-8 BOM (Excel writes one) and any trailing CR from a CRLF
    # file read in text mode on a stray platform.
    return str(value).replace("﻿", "").strip().strip("\r")


def _map_headers(headers):
    """Locate the pool / well / sample columns in a header row.

    Returns (index_by_role, unmatched_roles, ambiguous_roles). pool is
    optional. ambiguous_roles maps a role to every header that claimed it:
    a sheet carrying both `Sample` and `Sample_ID` is not a sheet to guess
    at, because taking the leftmost would emit a valid-looking rename_map
    built from the wrong column.
    """
    found = {}
    claims = {}
    for position, raw in enumerate(headers):
        name = _clean(raw).lower()
        if not name:
            continue
        for role, aliases in (
            ("pool", POOL_HEADERS),
            ("well", WELL_HEADERS),
            ("sample", SAMPLE_HEADERS),
        ):
            if name in aliases:
                claims.setdefault(role, []).append(name)
                if role not in found:
                    found[role] = position
    missing = [role for role in ("well", "sample") if role not in found]
    ambiguous = {role: names for role, names in claims.items() if len(names) > 1}
    return found, missing, ambiguous


def _read_csv_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [row for row in csv.reader(handle)]


def _read_xlsx_rows(path):
    """First worksheet of an .xlsx, as a list of string lists.

    Deliberately stdlib: openpyxl is not a pack dependency, and adding one
    for a cluster-deployed script means another thing to install on two
    clusters. Only what a submission sheet needs is handled -- shared
    strings, inline strings and numbers.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise LongPlexInputError(["sample map %s is not a readable .xlsx: %s" % (path, exc)])

    shared = []
    try:
        table = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in table.findall("%ssi" % XL_NS):
            shared.append("".join(t.text or "" for t in item.iter("%st" % XL_NS)))
    except KeyError:
        pass

    names = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
    if not names:
        raise LongPlexInputError(["sample map %s contains no worksheet" % path])
    sheet = ET.fromstring(archive.read(sorted(names)[0]))

    rows = []
    for row_element in sheet.iter("%srow" % XL_NS):
        cells = []
        for cell in row_element.findall("%sc" % XL_NS):
            # Honour the column reference, so a row with gaps does not shift
            # every value left into the wrong column.
            reference = cell.get("r") or ""
            letters = "".join(ch for ch in reference if ch.isalpha())
            column = 0
            for ch in letters:
                column = column * 26 + (ord(ch.upper()) - ord("A") + 1)
            column = max(column - 1, 0)
            while len(cells) < column:
                cells.append("")

            kind = cell.get("t")
            value_element = cell.find("%sv" % XL_NS)
            if kind == "s" and value_element is not None:
                try:
                    value = shared[int(value_element.text)]
                except (ValueError, IndexError):
                    value = ""
            elif kind == "inlineStr":
                inline = cell.find("%sis" % XL_NS)
                value = (
                    "".join(t.text or "" for t in inline.iter("%st" % XL_NS))
                    if inline is not None
                    else ""
                )
            else:
                value = value_element.text if value_element is not None else ""
            cells.append(value or "")
        rows.append(cells)
    return rows


def parse_sample_map(path):
    """(pool_or_None, well, sample_id) per data row, in sheet order.

    Raises LongPlexInputError if no header row can be located. Blank rows and
    the preamble rows a submission workbook carries above its header are
    skipped rather than treated as data.
    """
    extension = os.path.splitext(path)[1].lower()
    if extension in (".xlsx", ".xlsm"):
        rows = _read_xlsx_rows(path)
    elif extension in (".csv", ".tsv", ".txt", ""):
        rows = _read_csv_rows(path)
    else:
        raise LongPlexInputError(
            ["sample map %s has unsupported extension '%s' (want .csv or .xlsx)"
             % (path, extension)]
        )

    header_index = None
    columns = {}
    ambiguous = {}
    for index, row in enumerate(rows):
        candidate, missing, clashes = _map_headers(row)
        if not missing:
            header_index, columns, ambiguous = index, candidate, clashes
            break

    if ambiguous:
        raise LongPlexInputError([
            "row %d of %s has %d columns claiming to be the %s column (%s); "
            "rename the ones that are not, so there is nothing to guess at"
            % (header_index + 1, path, len(names), role, ", ".join(names))
            for role, names in sorted(ambiguous.items())
        ])

    if header_index is None:
        raise LongPlexInputError([
            "could not find a header row in %s: need a well column (one of %s) "
            "and a sample column (one of %s)"
            % (path, "/".join(WELL_HEADERS), "/".join(SAMPLE_HEADERS))
        ])

    records = []
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        def cell(role):
            position = columns.get(role)
            if position is None or position >= len(row):
                return ""
            return _clean(row[position])

        well, sample = cell("well"), cell("sample")
        if not well and not sample:
            continue  # blank separator row
        records.append({
            "row": offset,          # 1-based sheet row, for error messages
            "pool": cell("pool") or None,
            "well": well,
            "sample_id": sample,
        })
    return records


# ----------------------------------------------------------------------
# Assembly and validation
# ----------------------------------------------------------------------


def barcode_fasta_paths(barcode_dir, barcode_set):
    return (
        os.path.join(
            barcode_dir, BARCODE_FASTA_TEMPLATE.format(barcode_set=barcode_set, index=7)
        ),
        os.path.join(
            barcode_dir, BARCODE_FASTA_TEMPLATE.format(barcode_set=barcode_set, index=5)
        ),
    )


def _check_pool_ids(pools, errors):
    seen = {}
    for pool in pools:
        pool_id = pool.get("pool_ID") or ""
        if not POOL_ID_RE.match(pool_id):
            errors.append(
                "pool_ID %r is invalid: the LongPlex pipeline allows letters and "
                "digits only, so '_', '-' and '.' are rejected and it may not be "
                "empty" % pool_id
            )
        elif pool_id in seen:
            errors.append(
                "pool_ID %r appears twice in the manifest (datasets %s and %s); "
                "pool_ID prefixes every output path, so duplicates would collide"
                % (pool_id, seen[pool_id], pool.get("dataset_uuid"))
            )
        else:
            seen[pool_id] = pool.get("dataset_uuid")


def _check_pool_bams(pools, errors, filesystem):
    for pool in pools:
        path = pool.get("pool_path") or ""
        if not path:
            errors.append("pool %r has no pool_path" % pool.get("pool_ID"))
            continue
        if not path.endswith(".bam"):
            errors.append(
                "pool %r pool_path %r does not end in .bam; the pipeline's input "
                "schema rejects it" % (pool.get("pool_ID"), path)
            )
        size = filesystem.size_or_none(path)
        if size is None:
            # A missing BAM here means the transfer step silently failed, and
            # that is far cheaper to catch now than 40 minutes into Nextflow.
            errors.append(
                "pool %r BAM is missing at %s -- the transfer step did not put "
                "it there" % (pool.get("pool_ID"), path)
            )
        elif size == 0:
            errors.append(
                "pool %r BAM at %s is zero bytes -- the transfer step was "
                "interrupted" % (pool.get("pool_ID"), path)
            )


def _assign_pools(records, pool_ids, errors, warnings):
    """Attach a pool_ID to every sample-map row. See the module docstring."""
    if not records:
        return []

    explicit = [record for record in records if record["pool"]]
    if explicit:
        if len(explicit) != len(records):
            errors.append(
                "sample map has a pool column but %d of %d rows leave it blank; "
                "fill it in for every row or remove the column"
                % (len(records) - len(explicit), len(records))
            )
        return records

    if len(pool_ids) == 1:
        only = pool_ids[0]
        for record in records:
            record["pool"] = only
        return records

    errors.append(
        "the run has %d pools (%s) but the sample map has no pool column, so "
        "there is no way to tell which pool each row belongs to. Add a pool "
        "column (header 'pool', 'pool_ID' or 'barcode') naming the SMRT Link "
        "barcode, or supply one sample map per pool."
        % (len(pool_ids), ", ".join(pool_ids))
    )
    return records


def _check_records(records, pool_ids, errors, warnings):
    known = set(pool_ids)
    keys_seen = {}
    samples_seen = {}

    for record in records:
        row, well, sample_id = record["row"], record["well"], record["sample_id"]
        pool = record["pool"]

        if not WELL_RE.match(well):
            errors.append(
                "row %d: well %r is invalid. The LongPlex pipeline requires a "
                "letter A-H followed by a two-digit column 01-12 (so 'A1' and "
                "'A00' are both rejected, 'A01' is what it wants)." % (row, well)
            )
        if not sample_id:
            errors.append("row %d: sample_ID is empty" % row)
        elif not SAMPLE_ID_RE.match(sample_id):
            errors.append(
                "row %d: sample_ID %r contains a character that would break an "
                "output filename; letters, digits, '.', '_' and '-' are allowed"
                % (row, sample_id)
            )
        elif sample_id in samples_seen:
            errors.append(
                "row %d: sample_ID %r already used on row %d. Sample ids become "
                "output filenames, so a duplicate would have one pool's reads "
                "overwrite another's." % (row, sample_id, samples_seen[sample_id])
            )
        else:
            samples_seen[sample_id] = row

        if pool and pool not in known:
            # The pipeline filters rename_map per pool by pool_ID prefix and
            # says nothing when a prefix matches no pool -- that pool's whole
            # rename set just vanishes.
            errors.append(
                "row %d: pool %r is not one of the run's pools (%s); the "
                "pipeline would silently drop every rename with that prefix"
                % (row, pool, ", ".join(pool_ids) or "none")
            )

        if pool and WELL_RE.match(well):
            key = "%s.%s" % (pool, well)
            if key in keys_seen:
                errors.append(
                    "row %d: %s already mapped on row %d" % (row, key, keys_seen[key])
                )
            else:
                keys_seen[key] = row

    for pool_id in pool_ids:
        if not any(record["pool"] == pool_id for record in records):
            warnings.append(
                "pool %s has no rows in the sample map; its outputs will keep "
                "their default %s.<well> names" % (pool_id, pool_id)
            )


def build_rows(pools, records, i7_path, i5_path):
    """The two CSVs' rows, deterministically ordered.

    Byte-identical output for identical input is what makes `nextflow
    -resume` behave and makes a diff meaningful when someone reprocesses a
    pool, so both tables are sorted rather than left in API/sheet order.
    """
    pool_rows = [
        {
            "pool_ID": pool["pool_ID"],
            "pool_path": pool["pool_path"],
            "i7_barcode": i7_path,
            "i5_barcode": i5_path,
        }
        for pool in sorted(pools, key=lambda p: p["pool_ID"])
    ]
    rename_rows = [
        {
            "pool_ID.well_ID": "%s.%s" % (record["pool"], record["well"]),
            "sample_ID": record["sample_id"],
        }
        for record in sorted(records, key=lambda r: (r["pool"] or "", r["well"]))
    ]
    return pool_rows, rename_rows


def render_csv(columns, rows):
    """CSV text with LF line endings, so output does not vary by platform."""
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


class RealFilesystem:
    """The filesystem seam.

    Everything this script touches on disk goes through here, so the resolve
    -> validate -> render core can be exercised against fixtures, and so the
    day this moves hosts again only this class changes.
    """

    def size_or_none(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return None

    def is_readable_file(self, path):
        return os.path.isfile(path) and os.access(path, os.R_OK)

    def is_writable_dir(self, path):
        return os.path.isdir(path) and os.access(path, os.W_OK)

    def write_text(self, path, text):
        # Overwrite cleanly: re-running over an existing dest_dir is expected.
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)


def build_inputs(
    manifest,
    dest_dir,
    barcode_dir,
    barcode_set,
    sample_map=None,
    filesystem=None,
    write=True,
):
    """Validate everything, then emit the CSVs. The whole job, minus the CLI.

    Raises LongPlexInputError with every problem found. Nothing is written
    unless validation passes completely -- a half-written pool_sheet beside a
    rejected rename_map is worse than no sheet at all.
    """
    filesystem = filesystem or RealFilesystem()
    errors = []
    warnings = list(manifest.get("warnings") or [])

    if barcode_set not in BARCODE_SETS:
        raise LongPlexInputError([
            "barcode set %r is unknown; the pipeline ships %s"
            % (barcode_set, ", ".join(BARCODE_SETS))
        ])

    pools = list(manifest.get("pools") or [])
    if not pools:
        raise LongPlexInputError([
            "manifest lists no pools for run %r" % manifest.get("run_id")
        ])

    _check_pool_ids(pools, errors)
    _check_pool_bams(pools, errors, filesystem)

    i7_path, i5_path = barcode_fasta_paths(barcode_dir, barcode_set)
    for path in (i7_path, i5_path):
        if not filesystem.is_readable_file(path):
            errors.append("barcode FASTA %s is missing or unreadable" % path)

    if not filesystem.is_writable_dir(dest_dir):
        errors.append("dest_dir %s does not exist or is not writable" % dest_dir)

    pool_ids = sorted(
        pool["pool_ID"] for pool in pools if POOL_ID_RE.match(pool.get("pool_ID") or "")
    )

    records = []
    if sample_map:
        records = parse_sample_map(sample_map)
        if not records:
            warnings.append(
                "sample map %s has a header but no data rows; no rename_map "
                "will be written" % sample_map
            )
        else:
            records = _assign_pools(records, pool_ids, errors, warnings)
            _check_records(records, pool_ids, errors, warnings)

    if errors:
        raise LongPlexInputError(errors)

    pool_rows, rename_rows = build_rows(pools, records, i7_path, i5_path)

    pool_sheet_path = os.path.join(dest_dir, "pool_sheet.csv")
    rename_map_path = os.path.join(dest_dir, "rename_map.csv") if rename_rows else None

    if write:
        filesystem.write_text(pool_sheet_path, render_csv(POOL_SHEET_COLUMNS, pool_rows))
        if rename_map_path:
            filesystem.write_text(
                rename_map_path, render_csv(RENAME_MAP_COLUMNS, rename_rows)
            )

    per_pool_wells = {}
    for record in records:
        per_pool_wells[record["pool"]] = per_pool_wells.get(record["pool"], 0) + 1

    return {
        "pool_sheet": pool_sheet_path,
        "rename_map": rename_map_path,
        "pools": [
            {
                "pool_ID": pool["pool_ID"],
                "pool_path": pool["pool_path"],
                "dataset_uuid": pool.get("dataset_uuid"),
                # Only ever sheet-derived: the inner wells are invisible to
                # SMRT Link, so with no sample map there is nothing to count.
                "num_wells": per_pool_wells.get(pool["pool_ID"]) if records else None,
            }
            for pool in sorted(pools, key=lambda p: p["pool_ID"])
        ],
        "warnings": warnings,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build LongPlex pool_sheet.csv and rename_map.csv",
    )
    parser.add_argument("--pool-manifest", required=True,
                        help="JSON manifest from ductus.resolve_longplex_pools")
    parser.add_argument("--dest-dir",
                        help="Run working directory; the CSVs are written here. "
                             "Defaults to dest_dir in the manifest.")
    parser.add_argument("--barcode-dir", required=True,
                        help="The LongPlex pipeline's flat barcodes/ directory")
    parser.add_argument("--barcode-set", default=None, choices=list(BARCODE_SETS),
                        help="Defaults to barcode_set in the manifest, else set1")
    parser.add_argument("--sample-map", default=None,
                        help="Optional .csv/.xlsx giving well -> sample name. "
                             "Without it no rename_map is written and outputs "
                             "keep their pool_ID.well_ID names. An empty value "
                             "counts as not supplied, so a caller templating "
                             "the flag in does not need a conditional.")
    parser.add_argument("--output-json", default=None,
                        help="Write the result JSON here as well as to stdout")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        with open(args.pool_manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        print("ERROR: cannot read pool manifest %s: %s" % (args.pool_manifest, exc),
              file=sys.stderr)
        return 2

    dest_dir = args.dest_dir or manifest.get("dest_dir")
    if not dest_dir:
        print("ERROR: no --dest-dir given and the manifest has no dest_dir",
              file=sys.stderr)
        return 2
    barcode_set = args.barcode_set or manifest.get("barcode_set") or "set1"

    try:
        result = build_inputs(
            manifest=manifest,
            dest_dir=dest_dir,
            barcode_dir=args.barcode_dir,
            barcode_set=barcode_set,
            sample_map=(args.sample_map or "").strip() or None,
        )
    except LongPlexInputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    for warning in result["warnings"]:
        print("WARNING: %s" % warning, file=sys.stderr)
    print("wrote %s" % result["pool_sheet"], file=sys.stderr)
    if result["rename_map"]:
        print("wrote %s" % result["rename_map"], file=sys.stderr)

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
