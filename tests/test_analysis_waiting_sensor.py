from unittest import mock
from st2tests.base import BaseSensorTestCase
from sensors.processing_api_sensor import ProcessingApiSensor


class AnalysisWaitingSensorTestCase(BaseSensorTestCase):
    sensor_cls = ProcessingApiSensor

    CONFIG = {
        'processing_api_service_url': 'http://processing_api:8080/',
        'processing_api_analysis_next_url': 'api/v1/analysis/next/',
        'processing_api_access_key': 'processing_api_access_key',
        'transfer_settings_upload': {
            'marvin': {
                'upload_host': 'stackstorm.cgu.igp.uu.se',
                'upload_user': 'stanley',
                'upload_key': '/home/stanley/.ssh/id_ed25519',
                'upload_path_prefix': '/projects/inbox/',
                'upload_hospital_staging_path': '/ductus_staging/marvin_staging',
                'upload_copy_method': 'remote',
            },
            'miarka': {
                'upload_host': 'miarka1.uppmax.uu.se',
                'upload_user': 'funk_905',
                'upload_key': '/home/stanley/.ssh/funk_905_id_ed25519',
                'upload_path_prefix': '',
                'upload_hospital_staging_path': '/ductus_staging/miarka_staging',
                'upload_copy_method': 'remote',
            },
            'berk': {
                'upload_path_prefix': '/berkscale/cgu/inbox/',
                'upload_hospital_staging_path': '/ductus_staging/berk_staging',
                'upload_copy_method': 'local',
            },
        },
        'transfer_settings_download': {},
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
    def test_get_analysis_waiting_event_remote(self, mock_get):
        """Test that a trigger is dispatched when an analysis is available for upload."""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "260101-ABCDEFGHIJ",'
            '"process_location": "miarka"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.processing_api')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '260101-ABCDEFGHIJ')
        self.assertEqual(contexts[0]['payload']['event'], 'analysis_waiting')
        self.assertIn('timestamp', contexts[0]['payload'])

        payload = contexts[0]['payload']
        expected_settings = {
                'upload_host': 'miarka1.uppmax.uu.se',
                'upload_user': 'funk_905',
                'upload_key': '/home/stanley/.ssh/funk_905_id_ed25519',
                'upload_path_prefix': '',
                'upload_hospital_staging_path': '/ductus_staging/miarka_staging',
                'upload_copy_method': 'remote',
        }
        self.assertEqual({key: payload.get(key) for key in expected_settings}, expected_settings)

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_get_analysis_waiting_event_local(self, mock_get):
        """Test that a trigger is dispatched when an analysis is available for upload."""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "260202-KLMNOP",'
            '"process_location": "berk"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.processing_api')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '260202-KLMNOP')
        self.assertEqual(contexts[0]['payload']['event'], 'analysis_waiting')
        self.assertIn('timestamp', contexts[0]['payload'])

        payload = contexts[0]['payload']
        expected_settings = {
                'upload_host': '',
                'upload_user': '',
                'upload_key': '',
                'upload_path_prefix': '/berkscale/cgu/inbox/',
                'upload_hospital_staging_path': '/ductus_staging/berk_staging',
                'upload_copy_method': 'local',
        }
        self.assertEqual({key: payload.get(key) for key in expected_settings}, expected_settings)

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_get_analysis_waiting_event_no_data_exist(self, mock_get):
        """Test that no trigger is dispatched when no data is available"""
        contexts = self._setup_and_poll(mock_get, 204, '{}')

        self.assertEqual(len(contexts), 0)
