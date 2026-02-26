StackStorm pack to setup automation workflow for taking data from the
storage servers and delivering it to the processing cluster. Different
workflows will be by applied depending och which project the data
belongs to.


# How to run tests for packs


## Requirments

### System packages
* libldap2-dev
* libsasl2-dev

### Python libraries
´´´
pip3 install -r ~/PATH_TO/st2/requirements.txt
pip3 install jsonpickle
´´´


## Clone st2 repo

´´´
git clone https://github.com/StackStorm/st2.git

´´´

## Install requirements

´´´
export ST2_REPO_PATH=~/PATH_TO/st2

# -x: do not create virtualenv for test for tests, e.g. when running in existing one.
# -j: skip installing and updating the dependencies
st2-run-pack-tests  -p ./DUCTUS_PACKS_FOLDER  ./add-demultiplex-sensor -xj
´´´