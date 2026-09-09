#!/bin/bash
#
# Starts the seqWell LongPlex Nextflow pipeline, demultiplexing a run's pool
# BAMs into per-well BAMs and FASTQs.
#
# Same three-flag start-script interface the other pipelines on these
# clusters use:
#
#   bash run_longplex_demux.sh \
#     --inbox-path    <dir holding the run's pool BAMs> \
#     --samples-info  <pool_sheet.csv> \
#     --output        <dir for the pipeline's output> \
#     [--rename-map   <rename_map.csv>]
#
# --samples-info is the pipeline's OWN samplesheet -- pool_ID, pool_path,
# i7_barcode, i5_barcode -- as written by scripts/build_longplex_inputs.py.
# It is NOT the clinical Project,Run_nr,Sample_ID,Index_ID sheet that
# --sample-map means in scripts/reheader_pacbio_bams.sh and in
# build_longplex_inputs.py. Those two files are easy to confuse and the
# header check below exists to catch exactly that.
#
# Deployment target: both Marvin and Miarka, beside reheader_pacbio_bams.sh
# and build_longplex_inputs.py. One ductus-packs action per cluster invokes
# it, because the two share no submission mechanism:
#   - ductus.run_longplex_demux_marvin -- SSH (core.remote), runs this
#     script directly and synchronously, as run_analysis.yaml already
#     invokes runscripts on Marvin.
#   - ductus.run_longplex_demux_miarka -- via the Miarka processing-service
#     gateway (create_directory -> start_analysis -> poll status), which
#     invokes this script on our behalf and appends `parameters` to the
#     command line. Still ASSUMED rather than confirmed, the same caveat
#     reheader_pacbio_bams.sh carries.
#
# This script BLOCKS for the pipeline's whole duration and exits with
# nextflow's exit code. That is the entire failure signal: core.remote's
# failed() and the gateway's job status both read it.
#
# Deployment-specific values are environment variables with defaults rather
# than required flags, so the three-flag call above always works:
#
#   LONGPLEX_PIPELINE_DIR   the pipeline checkout (must contain main.nf)
#   LONGPLEX_PROFILE        nextflow -profile, default apptainer
#   LONGPLEX_SITE_CONFIG    optional extra -c config
#   LONGPLEX_MODULES        modules to load, default "nextflow apptainer"
#   LONGPLEX_RENAME_MAP     optional rename_map.csv, default none
#
# Each also has a flag (--pipeline-dir, --profile, --site-config, --work-dir,
# --rename-map) so a run can be steered by hand without exporting anything.
#
# Renaming outputs is the PIPELINE's job, through its optional rename_map
# parameter, which --rename-map forwards. Upstream builds a
# pool_ID.well_ID -> sample_ID dict from that CSV and names MERGE_READS'
# outputs ${meta.sample_ID}.bam / .fastq.gz, falling back to the pool.well
# key for any well the file does not mention. It also feeds
# RENAME_DEMUX_STATS, so the QC report carries the sample names too.
#
# That fallback is why this script validates the file so strictly: a key that
# matches nothing is not an error to the pipeline. A typo'd or stale pool
# prefix yields a successful run in which nothing was renamed, and the only
# symptom is output still named bc1015.A01.
#
# WITHOUT --rename-map the outputs keep their pool.well names, and giving
# them clinical sample names falls to ductus.reheader_pacbio_bams_*. NOTE for
# whoever wires that up: reheader_pacbio_bams.sh globs *.bam in ONE flat
# directory and reads the well from the second dot-separated field, so on a
# multi-pool run it must be invoked once per pool, with --inbox-path set to
# that pool's merged_bam directory -- and it does NOT compose with
# --rename-map, because a renamed BAM no longer has a well to parse.
#
# What this script deliberately does NOT do:
#   - it does not touch the SM: tag inside the BAMs. samtools merge keeps
#     whatever the pool's reads carried, so --rename-map changes filenames
#     and QC labels, not read groups. Fixing SM: is still
#     reheader_pacbio_bams.sh's job.
#   - it does not pass -with-report/-with-trace/-with-timeline/-with-dag. The
#     pipeline's own nextflow.config already enables all four, writing into
#     ${params.output}/logs/. Passing them here would fight that config.
#   - it does not clean up the nextflow work directory, which holds full
#     intermediate BAM copies and can be several times the input size. A
#     retention policy for it is still an open question; -resume needs it.

set -euo pipefail

readonly REQUIRED_COLUMNS="pool_ID pool_path i7_barcode i5_barcode"

# The columns holding paths that must exist before the pipeline starts.
readonly PATH_COLUMNS="pool_path i7_barcode i5_barcode"

# The optional rename map's two columns, as the pipeline's own
# schemas/rename_map_schema.json requires them.
readonly RENAME_MAP_COLUMNS="pool_ID.well_ID sample_ID"

usage() {
  cat >&2 <<'EOF'
Usage: run_longplex_demux.sh --inbox-path <pool_bam_dir> --samples-info <pool_sheet.csv> --output <output_dir>

  --inbox-path    directory holding the run's pool BAMs (the transfer's destination)
  --samples-info  the pipeline's pool_sheet.csv: pool_ID,pool_path,i7_barcode,i5_barcode
  --output        directory for the pipeline's output; created if absent
                  (--analysis-path is an accepted alias, for the Miarka gateway)

Optional:
  --rename-map    rename_map.csv: pool_ID.well_ID,sample_ID  [none]
  --pipeline-dir  LongPlex checkout containing main.nf   [$LONGPLEX_PIPELINE_DIR]
  --profile       nextflow -profile                      [apptainer]
  --work-dir      nextflow -work-dir                     [<output>/work]
  --site-config   extra nextflow -c config file          [none]
EOF
  exit 1
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# Every flag below takes a value. Without this, a flag left as the last
# token -- which is what an empty interpolation into the Miarka gateway's
# `parameters` string produces -- would fail on `shift 2` with bash's own
# "shift count out of range" under set -e, naming nothing useful.
need_value() {
  [ "$2" -ge 2 ] || die "$1 needs a value"
}

inbox_path=""
samples_info=""
output_path=""
analysis_path=""
pipeline_dir="${LONGPLEX_PIPELINE_DIR:-}"
profile="${LONGPLEX_PROFILE:-apptainer}"
site_config="${LONGPLEX_SITE_CONFIG:-}"
rename_map="${LONGPLEX_RENAME_MAP:-}"
work_dir=""

while [ $# -gt 0 ]; do
  case "$1" in
    --inbox-path)
      need_value "$1" $#
      inbox_path="$2"
      shift 2
      ;;
    --samples-info)
      need_value "$1" $#
      samples_info="$2"
      shift 2
      ;;
    --output)
      need_value "$1" $#
      output_path="$2"
      shift 2
      ;;
    # The Miarka processing-service's own field name for the same thing.
    # start_analysis sends analysis_path as a first-class field and is
    # assumed to pass it as --analysis-path (see
    # docs/pacbio_reheader_miarka_contract.md, which advises accepting both
    # forms rather than switching between them, so one deployed script can
    # serve both clusters).
    --analysis-path)
      need_value "$1" $#
      analysis_path="$2"
      shift 2
      ;;
    --rename-map)
      need_value "$1" $#
      rename_map="$2"
      shift 2
      ;;
    --pipeline-dir)
      need_value "$1" $#
      pipeline_dir="$2"
      shift 2
      ;;
    --profile)
      need_value "$1" $#
      profile="$2"
      shift 2
      ;;
    --work-dir)
      need_value "$1" $#
      work_dir="$2"
      shift 2
      ;;
    --site-config)
      need_value "$1" $#
      site_config="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

# --output and --analysis-path name the same directory. Two different
# values is a caller bug: silently picking one would write the run somewhere
# nobody is looking for it.
if [ -n "$analysis_path" ]; then
  if [ -n "$output_path" ] && [ "$output_path" != "$analysis_path" ]; then
    die "--output '$output_path' and --analysis-path '$analysis_path' name different directories; they are aliases for one another"
  fi
  output_path="$analysis_path"
fi

if [ -z "$inbox_path" ] || [ -z "$samples_info" ] || [ -z "$output_path" ]; then
  usage
fi

[ -d "$inbox_path" ] || die "--inbox-path '$inbox_path' is not a directory"
[ -f "$samples_info" ] || die "--samples-info '$samples_info' does not exist"
[ -s "$samples_info" ] || die "--samples-info '$samples_info' is empty"

# ---------------------------------------------------------------------------
# The samplesheet
#
# Columns are bound BY NAME, not by position: build_longplex_inputs.py writes
# one fixed order, but the pipeline's schema does not require it, so a
# hand-made sheet with the four columns in another order is legitimate input.
#
# Trailing CR is stripped throughout. An Excel-exported sheet is CRLF, and
# without this the last column of every row carries a \r -- which would
# report a barcode FASTA as missing when it is right there.
#
# Fields are not CSV-unquoted: build_longplex_inputs.py emits plain fields,
# and the pipeline's own schema refuses a path containing whitespace
# (^\S+\.bam$). A quoted field would be reported as a missing file rather
# than silently mis-parsed.
# ---------------------------------------------------------------------------

# Which of the named columns a CSV's header does NOT have. Used for both
# CSVs, so they cannot disagree about how a header is read: case-sensitive
# names, surrounding whitespace tolerated, trailing CR stripped.
missing_columns_in() {
  awk -F, -v required="$2" '
    NR == 1 {
      sub(/\r$/, "")
      for (i = 1; i <= NF; i++) {
        name = $i
        gsub(/^[ \t]+|[ \t]+$/, "", name)
        seen[name] = 1
      }
      n = split(required, want, " ")
      for (i = 1; i <= n; i++) if (!(want[i] in seen)) printf "%s ", want[i]
      exit
    }
  ' "$1"
}

# Non-blank rows after the header.
data_rows_in() {
  awk 'NR > 1 { sub(/\r$/, ""); if ($0 ~ /[^ \t,]/) n++ } END { print n + 0 }' "$1"
}

missing_columns=$(missing_columns_in "$samples_info" "$REQUIRED_COLUMNS")

if [ -n "$missing_columns" ]; then
  die "--samples-info '$samples_info' is missing the column(s): ${missing_columns% }
       This flag takes the pipeline's own pool_sheet.csv, whose header is
       'pool_ID,pool_path,i7_barcode,i5_barcode' (in any order), as written by
       build_longplex_inputs.py. It is NOT the clinical
       Project,Run_nr,Sample_ID,Index_ID sample sheet -- that one is used by
       the reheader step, after this one."
fi

data_rows=$(data_rows_in "$samples_info")

if [ "$data_rows" -eq 0 ]; then
  die "--samples-info '$samples_info' has a header but no data rows; there is nothing to demultiplex"
fi

# Every path the sheet references, emitted as "<row> <column> <path>" with tab
# separators so a path containing a space survives the round trip. Checked
# here rather than left to the pipeline: nextflow's own file-path validation
# would catch a missing BAM too, but only after the job has been queued and
# the containers pulled, and a zero-byte BAM -- what a truncated transfer
# leaves behind -- it would not catch at all.
while IFS=$'\t' read -r row column path; do
  [ -n "$path" ] || die "row $row of '$samples_info' has an empty $column"
  [ -e "$path" ] || die "row $row of '$samples_info' references a $column that does not exist: $path"
  [ -s "$path" ] || die "row $row of '$samples_info' references a zero-byte $column: $path"
done < <(
  awk -F, -v wanted="$PATH_COLUMNS" '
    NR == 1 {
      sub(/\r$/, "")
      for (i = 1; i <= NF; i++) {
        name = $i
        gsub(/^[ \t]+|[ \t]+$/, "", name)
        index_of[name] = i
      }
      split(wanted, want, " ")
      next
    }
    {
      sub(/\r$/, "")
      if ($0 !~ /[^ \t,]/) next
      for (i = 1; i in want; i++) {
        column = want[i]
        value = $(index_of[column])
        gsub(/^[ \t]+|[ \t]+$/, "", value)
        printf "%d\t%s\t%s\n", NR, column, value
      }
    }
  ' "$samples_info"
)

# ---------------------------------------------------------------------------
# The rename map (optional)
#
# Forwarded to the pipeline's own optional `rename_map` parameter. Upstream
# validates it against schemas/rename_map_schema.json:
#
#     pool_ID.well_ID   ^[A-Za-z0-9]+\.[A-H][0-9]{2}$
#     sample_ID         ^\S+$
#
# Everything below re-checks that BEFORE the pipeline starts, plus two things
# the pipeline cannot report at all, because an unmatched key is not an error
# to it -- the well just keeps its default name:
#
#   - a duplicate key. The pipeline builds a dict, so a repeat silently
#     last-wins. One well cannot be two samples, and picking either is a coin
#     toss on a clinical name.
#   - a key naming a pool that is not in the pool sheet. A typo'd or stale
#     prefix gives a completely successful run in which nothing was renamed.
#
# An empty value means "not supplied", the same convention --work-dir and
# --site-config use, so the st2 side can pass the flag unconditionally
# without a YAQL conditional deciding whether to include it.
# ---------------------------------------------------------------------------

if [ -n "$rename_map" ]; then
  [ -f "$rename_map" ] || die "--rename-map '$rename_map' does not exist"
  [ -s "$rename_map" ] || die "--rename-map '$rename_map' is empty"

  missing_rename_columns=$(missing_columns_in "$rename_map" "$RENAME_MAP_COLUMNS")
  if [ -n "$missing_rename_columns" ]; then
    die "--rename-map '$rename_map' is missing the column(s): ${missing_rename_columns% }
       Expected header 'pool_ID.well_ID,sample_ID', as written by
       build_longplex_inputs.py. This is neither the pool sheet nor the
       clinical Project,Run_nr,Sample_ID,Index_ID sample sheet."
  fi

  rename_rows=$(data_rows_in "$rename_map")
  if [ "$rename_rows" -eq 0 ]; then
    die "--rename-map '$rename_map' has a header but no data rows; drop the flag rather than passing an empty map"
  fi

  # Every pool the run actually has, to catch a key that would match nothing.
  #
  # This awk relies on the pool sheet having been validated already: if no
  # header field were `pool_ID`, `column` would be unset and `$column` would
  # expand to the whole line, making known_pools a list of CSV rows. It
  # cannot get here in that state -- missing_columns_in refused the sheet
  # further up -- but the awk reads as though it stands alone, so: it does
  # not.
  known_pools=$(
    awk -F, '
      NR == 1 {
        sub(/\r$/, "")
        for (i = 1; i <= NF; i++) {
          name = $i
          gsub(/^[ \t]+|[ \t]+$/, "", name)
          if (name == "pool_ID") column = i
        }
        next
      }
      {
        sub(/\r$/, "")
        if ($0 !~ /[^ \t,]/) next
        value = $column
        gsub(/^[ \t]+|[ \t]+$/, "", value)
        if (!(value in seen)) { seen[value] = 1; printf "%s ", value }
      }
    ' "$samples_info"
  )

  # Interval expressions ({2}) are not portable across every awk this may
  # meet, so the two-digit well is spelled out.
  rename_problems=$(
    awk -F, -v pools="$known_pools" '
      BEGIN {
        n = split(pools, list, " ")
        for (i = 1; i <= n; i++) known[list[i]] = 1
      }
      NR == 1 {
        sub(/\r$/, "")
        for (i = 1; i <= NF; i++) {
          name = $i
          gsub(/^[ \t]+|[ \t]+$/, "", name)
          if (name == "pool_ID.well_ID") key_column = i
          if (name == "sample_ID") sample_column = i
        }
        next
      }
      {
        sub(/\r$/, "")
        if ($0 !~ /[^ \t,]/) next

        key = $key_column
        sample = $sample_column
        gsub(/^[ \t]+|[ \t]+$/, "", key)
        gsub(/^[ \t]+|[ \t]+$/, "", sample)

        if (key !~ /^[A-Za-z0-9]+\.[A-H][0-9][0-9]$/) {
          printf "  row %d: key %s is not <pool_ID>.<well_ID> with a zero-padded well A01-H12\n", NR, key
        } else {
          pool = substr(key, 1, index(key, ".") - 1)
          if (!(pool in known)) {
            printf "  row %d: key %s names pool %s, which is not in the pool sheet (it has: %s)\n", NR, key, pool, pools
          }
        }

        if (key in seen) {
          printf "  row %d: key %s appears more than once\n", NR, key
        }
        seen[key] = 1

        if (sample == "") {
          printf "  row %d: key %s has an empty sample_ID\n", NR, key
        } else if (sample ~ /[ \t]/) {
          printf "  row %d: sample_ID \"%s\" contains whitespace\n", NR, sample
        }
      }
    ' "$rename_map"
  )

  if [ -n "$rename_problems" ]; then
    die "--rename-map '$rename_map' is not usable:
$rename_problems"
  fi
fi

# ---------------------------------------------------------------------------
# The pipeline and its runtime
# ---------------------------------------------------------------------------

[ -n "$pipeline_dir" ] || die "no LongPlex checkout given; set LONGPLEX_PIPELINE_DIR or pass --pipeline-dir"
[ -d "$pipeline_dir" ] || die "--pipeline-dir '$pipeline_dir' is not a directory"

# main.nf must be the pipeline's own, inside the pipeline's own tree: it
# calls samplesheetToList(params.pool_sheet, "schemas/input_schema.json"),
# a path relative to the project directory, so a copied main.nf cannot
# resolve its schema.
main_nf="${pipeline_dir%/}/main.nf"
[ -f "$main_nf" ] || die "no main.nf in --pipeline-dir '$pipeline_dir' (expected $main_nf)"

# rename_map is OPTIONAL and comparatively recent: the LongPlex-v3.1 tarball
# declares only pool_sheet and output, and its `version` file says 2.1.0 --
# exactly what a checkout that DOES have rename_map says -- so the version
# is no way to tell them apart. The pipeline's own schema is.
#
# This guard matters because the failure it prevents is silent: nextflow's
# validation.failUnrecognisedParams defaults to false, so an older checkout
# accepts --rename_map, ignores it, and names every output bc1015.A01
# instead of the sample, with a successful exit and nothing downstream to
# say why.
if [ -n "$rename_map" ]; then
  pipeline_schema="${pipeline_dir%/}/nextflow_schema.json"
  if [ ! -f "$pipeline_schema" ]; then
    die "--rename-map was given but there is no nextflow_schema.json in '$pipeline_dir', so whether this pipeline supports rename_map cannot be established"
  fi
  if ! grep -q '"rename_map"' "$pipeline_schema"; then
    die "the LongPlex checkout at '$pipeline_dir' does not declare a rename_map parameter, so --rename-map would be accepted and silently ignored, leaving every output named <pool_ID>.<well_ID>.
       Update the checkout to a version that has it, or drop --rename-map and rename downstream instead."
  fi
fi

if [ -n "$site_config" ]; then
  [ -f "$site_config" ] || die "--site-config '$site_config' does not exist"
fi

# The module system, if there is one. Guarded because `module` is often a
# shell function that a non-interactive shell has never sourced; when it is
# absent this is not itself fatal -- what matters is whether nextflow ends up
# on PATH, which is checked next and reported by name.
if command -v module >/dev/null 2>&1; then
  for name in ${LONGPLEX_MODULES-nextflow apptainer}; do
    module load "$name" || die "module load $name failed"
  done
fi

command -v nextflow >/dev/null 2>&1 || die "nextflow is not on PATH (module load failed or was skipped)"

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

output_path="${output_path%/}"
log_dir="${output_path}/logs"
[ -n "$work_dir" ] || work_dir="${output_path}/work"

mkdir -p "$output_path" "$log_dir" "$work_dir"

extra_config=()
if [ -n "$site_config" ]; then
  extra_config=(-c "$site_config")
fi

rename_param=()
if [ -n "$rename_map" ]; then
  rename_param=(--rename_map "$rename_map")
fi

echo "LongPlex demultiplexing"
echo "  inbox        : $inbox_path"
echo "  pool sheet   : $samples_info ($data_rows pool(s))"
echo "  output       : $output_path"
echo "  work dir     : $work_dir"
echo "  pipeline     : $main_nf"
echo "  profile      : $profile"
[ -n "$site_config" ] && echo "  site config  : $site_config"
if [ -n "$rename_map" ]; then
  echo "  rename map   : $rename_map ($rename_rows well(s) renamed; unlisted wells keep <pool_ID>.<well_ID>)"
else
  echo "  rename map   : none -- outputs keep their <pool_ID>.<well_ID> names"
fi

# -log is a nextflow option and must precede `run`; after it, it would be
# handed to the workflow and rejected. -resume with a stable -work-dir is
# what makes a re-run after a timeout or a dropped session cheap instead of
# a restart from the beginning.
set +e
nextflow \
  -log "${log_dir}/nextflow.log" \
  run "$main_nf" \
  -profile "$profile" \
  -work-dir "$work_dir" \
  -resume \
  ${extra_config[@]+"${extra_config[@]}"} \
  --pool_sheet "$samples_info" \
  ${rename_param[@]+"${rename_param[@]}"} \
  --output "$output_path"
status=$?
set -e

if [ "$status" -ne 0 ]; then
  echo "ERROR: nextflow exited $status; see ${log_dir}/nextflow.log" >&2
  exit "$status"
fi

echo "LongPlex demultiplexing finished; output in $output_path"
