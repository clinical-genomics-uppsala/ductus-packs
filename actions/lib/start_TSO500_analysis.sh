#!/bin/bash

EXPERIMENT_NAME=${1}
YEAR=${2}
PROJECT_TYPE=${3}
TSO500_VERSION=${4}

PATH_INPUT_INBOX="/projects/wp1/nobackup/ngs/${PROJECT_TYPE}/INBOX_TSO500/"
PATH_INPUT_OUTBOX="/projects/wp1/nobackup/ngs/${PROJECT_TYPE}/OUTBOX_TSO500/${EXPERIMENT_NAME}/"
PATH_ANALYSIS_FOLDER="/beegfs/wp1/nobackup/ngs/${PROJECT_TYPE}/analys/${YEAR}/${EXPERIMENT_NAME}/"
PATH_TSO500="/sw/pipelines/TSO500/${TSO500_VERSION}/"

mkdir ${PATH_ANALYSIS_FOLDER}
rsync -Prv ${PATH_INPUT_INBOX}/*.csv ${PATH_INPUT_INBOX}*.xml ${PATH_ANALYSIS_FOLDER}

cd ${PATH_ANALYSIS_FOLDER}
rsync -Prv ${PATH_TSO500}/DATA .
module add slurm-drmaa
snakemake -p -j 1 --drmaa "-A wp4 -s -p core -n 1 -t 2:00:00 "  -s ${PATH_TSO500}/src/Snakemake/rules/TSO500_yaml/TSO500_yaml.smk

snakemake -p -j 64 --drmaa "-A wp4 -s -p core -n {cluster.n} -t {cluster.time}"  -s ${PATH_TSO500}/TSO500.smk --use-singularity --singularity-args "--bind /data --bind /beegfs  --bind /projects " --cluster-config Config/Slurm/cluster.json

mkdir ${PATH_INPUT_OUTBOX}
rsync -Prv DNA_BcBio DNA_TSO500 RNA_TST170 Results fastq ${PATH_INPUT_OUTBOX}
touch ${PATH_INPUT_OUTBOX}/Done.txt
