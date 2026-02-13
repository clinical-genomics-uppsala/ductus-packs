from st2reactor.sensor.base import PollingSensor
from processing_api_client import ProcessingApiClient
from datetime import datetime, timezone

class DemultiplexingProcessingApiSensor(PollingSensor):

    def __init__(self, sensor_service, config=None, poll_interval=None, trigger='ductus.demultiplexing_processing_api'):
        super(DemultiplexingProcessingApiSensor, self).__init__(sensor_service=sensor_service,
                                              config=config,
                                              poll_interval=poll_interval)
        self._logger = self._sensor_service.get_logger(__name__)
        self._infolog("__init__")
        self._client = None
        self._trigger = trigger

    def setup(self):
        self._infolog("setup")
        base_url = self._config["processing_api_service_url"].rstrip("/")
        path = self._config["processing_api_demultiplexing_next_url"].lstrip("/")
        client_urls = "{}/{}".format(base_url, path)
        api_key = self._config["processing_api_access_key"]
        self._client = ProcessingApiClient(client_urls, api_key, self._logger)
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

        demultiplexing_data = result['response']
        payload = {
            **demultiplexing_data,
            'event': 'demultiplexing_waiting',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload, trace_tag=demultiplexing_data['sequencerun_id'])

    def _infolog(self, msg):
        self._logger.info("[ductus-packs." + self.__class__.__name__ + "] " + msg)
