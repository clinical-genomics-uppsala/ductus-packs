#!/usr/bin/env bash

PANEL_NAME=${1}
RUNFOLDER_DIRECTORY=${2}

alias whoami="echo Andrei Alexsson"
BCBIO_BIN=/sw/pipelines/bcbio-nextgen/1.1.5/anaconda/bin
BCBIO_LOCAL_BIN=/sw/pipelines/bcbio-nextgen/1.1.5/usr/local/bin
export JAVA_HOME=/sw/compilers/oracle-jdk-1.8/1.8.0_162
export BCBIO_JAVA_HOME=/sw/compilers/oracle-jdk-1.8/1.8.0_162
export PATH=$JAVA_HOME/bin:$BCBIO_BIN:$BCBIO_LOCAL_BIN:$PATH


cd ${RUNFOLDER_DIRECTORY}
/projects/wp3/Script/${PANEL_NAME}/run_everything_script.sh
