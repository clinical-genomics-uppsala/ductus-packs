from unittest import mock
from st2tests.base import BaseSensorTestCase
from sensors.upload_completed_sensor import UploadCompletedSensor

class UploadCompletedSensorTestCase(BaseSensorTestCase):
    sensor_cls = UploadCompletedSensor

    CONFIG = {
        'processing_api_service_url': 'http://processing_api:8080/',
        'processing_api_upload_completed_next_url': 'api/v1/analysis/upload-completed/next/',
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
    def test_fetch_upload_completed_event(self, mock_get):
        """Test that a trigger is dispatched when an upload has completed"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "240101_A00123_0001_ABCDEFGHIJ"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.upload_completed')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '240101_A00123_0001_ABCDEFGHIJ')
        self.assertEqual(contexts[0]['payload']['event'], 'upload_completed')
        self.assertIn('timestamp', contexts[0]['payload'])

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_upload_completed_event_second_analysis(self, mock_get):
        """Test that a trigger is dispatched for a different analysis"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "240202_B00456_0002_BCDEFGHIJK"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.upload_completed')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '240202_B00456_0002_BCDEFGHIJK')
        self.assertEqual(contexts[0]['payload']['event'], 'upload_completed')
        self.assertIn('timestamp', contexts[0]['payload'])

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_upload_completed_event_no_data_exist(self, mock_get):
        """Test that no trigger is dispatched when no data is available"""
        contexts = self._setup_and_poll(mock_get, 204, '{}')

        self.assertEqual(len(contexts), 0)
