#!/bin/bash

EXPERIMENT_NAME=${1}
YEAR=${2}
SERA_VERSION=${3}
INPUT_FILE=${5}
ANALYSIS=${6}
GLOBALS=${7}
NORMAL=${8}
PROJECT=${9}
PROJECT_TYPE=${10}
REFDIR=${11}

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
