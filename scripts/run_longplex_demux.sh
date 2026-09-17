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
#     --output        <dir for the pipeline's output>
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
#
# Each also has a flag (--pipeline-dir, --profile, --site-config, --work-dir)
# so a run can be steered by hand without exporting anything.
#
# The pipeline names its own outputs <pool_ID>.<well_ID>.bam / .fastq.gz;
# giving them clinical sample names falls entirely to
# ductus.reheader_pacbio_bams_*. NOTE for whoever wires that up:
# reheader_pacbio_bams.sh globs *.bam in ONE flat directory and reads the
# well from the second dot-separated field, so on a multi-pool run it must
# be invoked once per pool, with --inbox-path set to that pool's merged_bam
# directory.
#
# This deployment (LongPlex v3.1) declares only --pool_sheet and --output --
# no rename_map parameter, so this script does not offer one either. Upstream
# seqwell/LongPlex's main branch has since added an optional rename_map, but
# forwarding it here would be speculative support for a checkout this pack
# does not run: both versions report the same `version` file (2.1.0), so a
# deployed checkout cannot be trusted to have it just because a newer one
# does. If the deployed pipeline is ever upgraded past v3.1, revisit this.
#
# What this script deliberately does NOT do:
#   - it does not touch the SM: tag inside the BAMs. samtools merge keeps
#     whatever the pool's reads carried; fixing SM: is
#     reheader_pacbio_bams.sh's job, downstream of this script entirely.
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


usage() {
  cat >&2 <<'EOF'
Usage: run_longplex_demux.sh --inbox-path <pool_bam_dir> --samples-info <pool_sheet.csv> --output <output_dir>

  --inbox-path    directory holding the run's pool BAMs (the transfer's destination)
  --samples-info  the pipeline's pool_sheet.csv: pool_ID,pool_path,i7_barcode,i5_barcode
  --output        directory for the pipeline's output; created if absent
                  (--analysis-path is an accepted alias, for the Miarka gateway)

Optional:
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

echo "LongPlex demultiplexing"
echo "  inbox        : $inbox_path"
echo "  pool sheet   : $samples_info ($data_rows pool(s))"
echo "  output       : $output_path"
echo "  work dir     : $work_dir"
echo "  pipeline     : $main_nf"
echo "  profile      : $profile"
[ -n "$site_config" ] && echo "  site config  : $site_config"

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
  --output "$output_path"
status=$?
set -e

if [ "$status" -ne 0 ]; then
  echo "ERROR: nextflow exited $status; see ${log_dir}/nextflow.log" >&2
  exit "$status"
fi

echo "LongPlex demultiplexing finished; output in $output_path"
