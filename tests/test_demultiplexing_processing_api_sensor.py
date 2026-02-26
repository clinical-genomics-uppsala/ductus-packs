from unittest import mock
from st2tests.base import BaseSensorTestCase
from sensors.demultiplexing_processing_api_sensor import DemultiplexingProcessingApiSensor

class DemultiplexingProcessingApiSensorTestCase(BaseSensorTestCase):
    sensor_cls = DemultiplexingProcessingApiSensor

    CONFIG = {
        'processing_api_service_url': 'http://processing_api:8080/',
        'processing_api_demultiplexing_next_url': 'api/v1/sequencerun/demultiplex/next',
        'processing_api_access_key': 'asdajl3j5ads'
    }

    def _setup_and_poll(self, mock_get, status_code, response_text):
        mock_response = mock.MagicMock()
        mock_response.status_code = status_code
        mock_response.text = response_text
        mock_get.return_value = mock_response

        sensor = self.get_sensor_instance(config=self.CONFIG)
        sensor.setup()
        sensor.poll()
        return self.get_dispatched_triggers()

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_waiting_demultiplexing_event_instrument(self, mock_get):
        """Test that a trigger is dispatched for instrument demultiplexing"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"sequence_run": "231103_LH01573_14_AAC7KTKHV", "demultiplex_location": "DI"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.demultiplexing_processing_api')
        self.assertEqual(contexts[0]['payload']['sequence_run'], '231103_LH01573_14_AAC7KTKHV')
        self.assertEqual(contexts[0]['payload']['demultiplex_location'], 'DI')
        self.assertEqual(contexts[0]['payload']['event'], 'demultiplexing_waiting')
        self.assertIn('timestamp', contexts[0]['payload'])

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_waiting_demultiplexing_event_server(self, mock_get):
        """Test that a trigger is dispatched for server demultiplexing"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"sequence_run": "231103_LH01573_15_BBC7KTKHV", "demultiplex_location": "DS"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.demultiplexing_processing_api')
        self.assertEqual(contexts[0]['payload']['sequence_run'], '231103_LH01573_15_BBC7KTKHV')
        self.assertEqual(contexts[0]['payload']['demultiplex_location'], 'DS')
        self.assertEqual(contexts[0]['payload']['event'], 'demultiplexing_waiting')
        self.assertIn('timestamp', contexts[0]['payload'])

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_waiting_demultiplexing_event_no_data_exist(self, mock_get):
        """Test that no trigger is dispatched when no data is available"""
        contexts = self._setup_and_poll(mock_get, 204, '{}')

        self.assertEqual(len(contexts), 0)
