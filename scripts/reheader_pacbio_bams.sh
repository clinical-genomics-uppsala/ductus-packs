#!/bin/bash
#
# Reheaders demultiplexed PacBio LongPlex BAMs, replacing each BAM's SM: tag
# with the correct sample id looked up from a well-id -> sample-id map.
#
# Deployment target: both Marvin and Miarka. One ductus-packs action per
# cluster invokes it, because the two share no submission mechanism:
#   - ductus.reheader_pacbio_bams_marvin -- SSH (core.remote), runs
#     `bash <this script> --inbox-path ... --analysis-path ... --sample-map ...`
#     directly and synchronously. This calling convention is CONFIRMED: it's
#     how run_analysis.yaml already invokes runscripts on Marvin.
#   - ductus.reheader_pacbio_bams_miarka -- via the Miarka processing-service
#     gateway (create_directory -> start_analysis -> poll status), which
#     invokes this script on our behalf.
#
# The Miarka invocation is the one still ASSUMED, not confirmed. Its
# start_analysis payload sends three relevant fields:
#   - inbox_path     -> the source directory of demultiplexed BAMs
#   - analysis_path  -> the output directory for reheadered BAMs
#   - parameters     -> a single string, assumed appended as extra CLI args
#                        (matching how process_settings_miarka's `parameters`
#                        is used for real analyses), set to
#                        "--sample-map <path>" by the calling workflow.
# Future maintainer: verify how the processing-service actually invokes a
# run_script (flags vs positional args vs env vars vs a parameters file). If
# it differs, make the parser below accept both forms rather than switching
# it -- the Marvin path depends on the named flags.

set -euo pipefail

usage() {
  echo "Usage: $0 --inbox-path <demuxed_bam_dir> --analysis-path <reheader_dir> --sample-map <sample_map.csv>" >&2
  exit 1
}

inbox_path=""
analysis_path=""
sample_map=""

while [ $# -gt 0 ]; do
  case "$1" in
    --inbox-path)
      inbox_path="$2"
      shift 2
      ;;
    --analysis-path)
      analysis_path="$2"
      shift 2
      ;;
    --sample-map)
      sample_map="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [ -z "$inbox_path" ] || [ -z "$analysis_path" ] || [ -z "$sample_map" ]; then
  usage
fi

module load samtools

if [ ! -d "$analysis_path" ]; then
    mkdir -p "$analysis_path"
fi

find "$inbox_path"/*.bam | while read -r bam; do

    echo "Processing $bam"

    well_id=$(basename "$bam" .bam | cut -d'.' -f2)
    echo "Well ID: $well_id"

    # Exact, field-aware lookup, and it must resolve to exactly one row.
    #
    # A substring grep over the whole line (what this used to do) matches A01
    # inside A010, or inside an unrelated value in some other column -- which
    # would reheader a clinical sample with another sample's SM tag. Several
    # matches also used to collapse into a multi-line sample_id, corrupting
    # both the SM tag and the output filename; no match used to `continue`,
    # so the BAM was silently never produced and the job still exited 0.
    # Zero or duplicate matches are an operator problem: fail loudly.
    #
    # well_id is compared against every field rather than one fixed column,
    # because only the sample-id column index (3) is known for this file
    # format. Exact whole-field equality is what matters here; pin the well
    # column too if that index is ever confirmed. Trailing CR is stripped so
    # an Excel-exported (CRLF) sample map does not leak \r into the SM tag or
    # the output filename.
    match_count=$(awk -F, -v well="$well_id" '
        { sub(/\r$/, ""); for (i = 1; i <= NF; i++) if ($i == well) { n++; break } }
        END { print n + 0 }
    ' "$sample_map")

    if [ "$match_count" -ne 1 ]; then
        echo "ERROR: well id '$well_id' matched $match_count rows in $sample_map (need exactly 1)" >&2
        exit 1
    fi

    sample_id=$(awk -F, -v well="$well_id" '
        { sub(/\r$/, ""); for (i = 1; i <= NF; i++) if ($i == well) { print $3; exit } }
    ' "$sample_map")

    if [ -z "$sample_id" ]; then
        echo "ERROR: the row matching well id '$well_id' in $sample_map has an empty sample id (column 3)" >&2
        exit 1
    fi
    echo "Sample ID: $sample_id"

    # Reheader the BAM file to change the sample name
    echo "samtools view - sed - samtools reheader started..."
    samtools view -H "$bam" | \
    sed "s/\tSM:[^[:space:]]*/\tSM:$sample_id/" | \
    samtools reheader - "$bam" > "${analysis_path}/${sample_id}.bam"
    echo "finished processing"
done
