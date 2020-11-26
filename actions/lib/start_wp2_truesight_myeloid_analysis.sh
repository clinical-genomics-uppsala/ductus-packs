#!/bin/bash

EXPERIMENT_NAME=${1}
YEAR=${2}
CURRENT_DATE=${3}
WP2_ANALYSIS_VERSION=${4}

PATH_INPUT_FILES="/projects/wp1/ngs/klinik/sample_files/${YEAR}"
PATH_ANALYSIS_FOLDER="/projects/wp2/TruSight_Myeloid/Analyses/${YEAR}/${EXPERIMENT_NAME}"

#module load wp2_analysis/${WP2_ANALYSIS_VERSION}

SCRIPTS="/projects/wp2/TruSight_Myeloid/scripts"

echo "TruSight_Run_Moriarty.sh -m ${PATH_ANALYSIS_FOLDER} -d \"${CURRENT_DATE}\""

cd $SCRIPTS
bash TruSight_Run_Moriarty.sh -m ${PATH_ANALYSIS_FOLDER} -d "${CURRENT_DATE}"
