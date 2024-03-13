from st2reactor.sensor.base import PollingSensor
from archiving_client import ArchivingClient
from datetime import datetime

class ArchivingSensor(PollingSensor):

    def __init__(self, sensor_service, config=None, poll_interval=None, trigger='ductus.archiving_event'):
        super(ArchivingSensor, self).__init__(sensor_service=sensor_service,
                                              config=config,
                                              poll_interval=poll_interval)
        self._logger = self._sensor_service.get_logger(__name__)
        self._infolog("__init__")
        self._client = None
        self._trigger = trigger
        self._hostconfigs = {}

    def setup(self):
        self._infolog("setup")
        client_urls = self._config["processing_api_service_url"] + self._config["processing_api_sequence_run_next_archive_url"]
        self._client = ArchivingClient(client_urls, self._logger)
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
        
        analysis_data = result['response']
        payload = {
            **analysis_data,
            'timestamp': datetime.utcnow().isoformat(),
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload, trace_tag=analysis_data['analysis_name'])

    def _infolog(self, msg):
        self._logger.info("[ductus-packs." + self.__class__.__name__ + "] " + msg)