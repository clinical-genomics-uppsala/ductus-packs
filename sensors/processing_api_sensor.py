from st2reactor.sensor.base import PollingSensor
from processing_api_client import ProcessingApiClient
from datetime import datetime
import yaml
import os

class ProcessingApiSensor(PollingSensor):

    def __init__(self, sensor_service, config=None, poll_interval=None, trigger='ductus.processing_api_created'):
        super(ProcessingApiSensor, self).__init__(sensor_service=sensor_service,
                                              config=config,
                                              poll_interval=poll_interval)
        self._logger = self._sensor_service.get_logger(__name__)
        self._infolog("__init__")
        self._client = None
        self._trigger = trigger
        self._hostconfigs = {}

    def setup(self):
        self._infolog("setup")
        client_urls = self._config["processing_api_service_url"]
        self._client = ProcessingApiClient(client_urls, self._logger)
        self._infolog("Created client: {0}".format(self._client))
        self._infolog("setup finished")

    def poll(self):
        self._infolog("poll")
        self._infolog("Checking api for new entries")
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
        
        
        #runfolder_name = os.path.split(runfolder_path)[1]
        # payload = {
        #     'analysis': result['response']['analysis'],
        #     'analysis_name': result['response']['analysis_name'],
        #     'created_date': result['response']['created_date'],
        #     'done_date': result['response']['done_date'],
        #     'last_uodate': result['response']['last_update'],
        #     'priority': result['response']['priority'],
        #     'progress': result['response']['progress'],
        #     'status': result['response']['status'],
        #     'workpackage': result['response']['workpackage'],
        #     'timestamp': datetime.utcnow().isoformat(),
        # }
        analysis_data = result['response'].json()
        payload = {
            **analysis_data,
            'timestamp': datetime.utcnow().isoformat(),
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload, trace_tag=analysis_data['analysis_name'])

    def _infolog(self, msg):
        self._logger.info("[ductus-packs." + self.__class__.__name__ + "] " + msg)