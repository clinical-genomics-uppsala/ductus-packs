#!/bin/bash

ANALYSIS_FOLDER_PATH=${1}
DATE=${2}

echo "./projects/wp2/TruSight_Myeloid/scripts/TruSight_Run_Moriarty.sh -m ${ANALYSIS_FOLDER_PATH} -d \"${DATE}\""


./projects/wp2/TruSight_Myeloid/scripts/TruSight_Run_Moriarty.sh \
    -m ${ANALYSIS_FOLDER_PATH} \
    -d "${DATE}"


