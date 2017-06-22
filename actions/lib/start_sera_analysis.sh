#!/bin/bash

EXPERIMENT_NAME=${1}
YEAR=${2}
SERA_VERSION=${3}
SLURM_VERSION=${4}
INPUT_FILE=${5}
ANALYSIS=${6}
GLOBALS=${7}
NORMAL=${8}
PROJECT=${9}
REFDIR=${10}

PATH_INPUT_FILES="/projects/wp1/ngs/klinik/sample_files/${YEAR}"
PATH_ANALYSIS_FOLDER="/projects/wp1/ngs/klinik/analys/${YEAR}/${EXPERIMENT_NAME}"

module load slurm/${SLURM_VERSION}
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


