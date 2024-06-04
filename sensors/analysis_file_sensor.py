from st2reactor.sensor.base import PollingSensor
from analysis_file_client import AnalysisFileClient
from datetime import datetime
import csv

def parse_file(filename):
    with open(filename, mode='r') as file:
        csv_reader = csv.DictReader(file)
        experiments = set()

        for row in csv_reader:
            # Collect unique experiments
            experiments.add(row['Experiment'])

    return experiments

class AnalysisFileSensor(PollingSensor):

    def __init__(self, sensor_service, config=None, poll_interval=None, trigger='ductus.analysis_file'):
        super(AnalysisFileSensor, self).__init__(sensor_service=sensor_service,
                                              config=config,
                                              poll_interval=poll_interval)
        self._logger = self._sensor_service.get_logger(__name__)
        self._infolog("__init__")
        self._client = None
        self._trigger = trigger
        self._hostconfigs = {}

    def setup(self):
        self._infolog("setup")
        folders = self._config["analysis_file_folders"]
        self._client = AnalysisFileClient(folders, self._logger)
        self._infolog("Created client: {0}".format(self._client))
        self._infolog("setup finished")

    def poll(self):
        self._infolog("poll")
        self._infolog("Checking folder for new files")
        result = self._client.next_ready()
        self._infolog("Result from client: {0}".format(result))
        if result:
            self._handle_result(result)

    def cleanup(self):
        self._infolog("cleanup")

    def add_trigger(self, trigger):
        self._infolog("add_trigger")

    def update_trigger(self, trigger):
        self._infolog("update_trigger")

    def remove_trigger(self, trigger):
        self._infolog("remove_trigger")

    def _handle_result(self, result):
        self._infolog("_handle_result")
        trigger = self._trigger
        
        analysis_file = result['analysis_file']

        experiments = parse_file(analysis_file)
        payload = {
            'analysis_file': analysis_file,
            'timestamp': datetime.utcnow().isoformat(),
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload, trace_tag=", ".join(experiments))

    def _infolog(self, msg):
        self._logger.info("[ductus-packs." + self.__class__.__name__ + "] " + msg)