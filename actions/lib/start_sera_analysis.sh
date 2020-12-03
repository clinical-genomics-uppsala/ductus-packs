#!/bin/bash

EXPERIMENT_NAME=${1}
YEAR=${2}
SERA_VERSION=${3}
INPUT_FILE=${4}
ANALYSIS=${5}
GLOBALS=${6}
NORMAL=${7}
PROJECT=${8}
PROJECT_TYPE=${9}
REFDIR=${10}

PATH_INPUT_FILES="/projects/wp1/nobackup/ngs/${PROJECT_TYPE}/sample_files/${YEAR}"
PATH_ANALYSIS_FOLDER="/projects/wp1/nobackup/ngs/${PROJECT_TYPE}/analys/${YEAR}/${EXPERIMENT_NAME}"

module load sera/${SERA_VERSION}

echo "createInputFile_moriarty.py -a ${ANALYSIS} -g ${GLOBALS} -i ${PATH_INPUT_FILES}/${INPUT_FILE} -n ${NORMAL} -p ${PROJECT} -refDir ${REFDIR}"

createInputFile_moriarty.py \
    -a ${ANALYSIS} \
    -g ${GLOBALS} \
    -i ${PATH_INPUT_FILES}/${INPUT_FILE} \
    -n ${NORMAL} \
    -p ${PROJECT} \
    -refDir ${REFDIR}

echo "SERA_PBS_SUBMIT.sh -p ${PATH_ANALYSIS_FOLDER} -i ${PATH_ANALYSIS_FOLDER}/inputFile"

echo "start" | SERA_PBS_SUBMIT.sh -p ${PATH_ANALYSIS_FOLDER} -i ${PATH_ANALYSIS_FOLDER}/inputFile
