import copy
from unittest import mock
from st2tests.base import BaseSensorTestCase
from sensors.download_available_sensor import DownloadAvailableSensor


class DownloadAvailableSensorTestCase(BaseSensorTestCase):
    sensor_cls = DownloadAvailableSensor

    CONFIG = {
        'processing_api_service_url': 'http://processing_api:8080/',
        'processing_api_download_available_next_url': 'api/v1/analysis/download-available/next/',
        'processing_api_access_key': 'asdajl3j5ads',
        'transfer_settings_download': {
            'miarka': {
                'download_host': 'miarka1.uppmax.uu.se',
                'download_user': 'funk_907',
                'download_key': '/home/stanley/.ssh/funk_907_id_ed25519',
                'download_path_prefix': '',
                'download_copy_method': 'remote',
            },
            'berk': {
                'download_path_prefix': 'outbox/',
                'download_copy_method': 'local',
            },
        },
        'transfer_settings_upload': {},
    }

    def _setup_and_poll(self, mock_get, status_code, response_text, config=None):
        mock_response = mock.MagicMock()
        mock_response.status_code = status_code
        mock_response.text = response_text
        mock_get.return_value = mock_response

        sensor = self.get_sensor_instance(config=config or self.CONFIG)
        sensor.setup()
        sensor.poll()
        return self.get_dispatched_triggers()

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_download_available_event_remote(self, mock_get):
        """Test that a trigger is dispatched when an analysis is available for download"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "240101_A00123_0001_ABCDEFGHIJ",'
            '"process_location": "miarka"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.download_available')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '240101_A00123_0001_ABCDEFGHIJ')
        self.assertEqual(contexts[0]['payload']['event'], 'download_available')
        self.assertIn('timestamp', contexts[0]['payload'])

        payload = contexts[0]['payload']
        expected_settings = {
                'download_host': 'miarka1.uppmax.uu.se',
                'download_user': 'funk_907',
                'download_key': '/home/stanley/.ssh/funk_907_id_ed25519',
                'download_path_prefix': '',
                'download_copy_method': 'remote',
        }
        self.assertEqual({key: payload.get(key) for key in expected_settings}, expected_settings)

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_download_available_event_second_analysis(self, mock_get):
        """Test that a trigger is dispatched for a different analysis"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "240202_B00456_0002_BCDEFGHIJK",'
            '"process_location": "miarka"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.download_available')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '240202_B00456_0002_BCDEFGHIJK')
        self.assertEqual(contexts[0]['payload']['event'], 'download_available')
        self.assertIn('timestamp', contexts[0]['payload'])

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_download_available_event_local(self, mock_get):
        """Test that a trigger is dispatched when an analysis is available for download"""
        contexts = self._setup_and_poll(
            mock_get, 200,
            '{"analysis_name": "260202-KLMNOP",'
            '"process_location": "berk"}'
        )

        self.assertGreater(len(contexts), 0)
        self.assertEqual(contexts[0]['trigger'], 'ductus.download_available')
        self.assertEqual(contexts[0]['payload']['analysis_name'], '260202-KLMNOP')
        self.assertEqual(contexts[0]['payload']['event'], 'download_available')
        self.assertIn('timestamp', contexts[0]['payload'])

        payload = contexts[0]['payload']
        expected_settings = {
                'download_host': '',
                'download_user': '',
                'download_key': '',
                'download_path_prefix': 'outbox/',
                'download_copy_method': 'local',
        }
        self.assertEqual({key: payload.get(key) for key in expected_settings}, expected_settings)

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_download_available_event_no_data_exist(self, mock_get):
        """Test that no trigger is dispatched when no data is available"""
        contexts = self._setup_and_poll(mock_get, 204, '{}')

        self.assertEqual(len(contexts), 0)

    @mock.patch('sensors.processing_api_client.requests.get')
    def test_fetch_download_available_event_missing_keys(self, mock_get):
        """Test that a remote location without host, user and key raises instead of dispatching"""
        config = copy.deepcopy(self.CONFIG)
        config['transfer_settings_download']['berk']['download_copy_method'] = 'remote'

        with self.assertRaisesRegex(ValueError, 'download_host, download_user, download_key'):
            self._setup_and_poll(
                mock_get, 200,
                '{"analysis_name": "260202-KLMNOP",'
                '"process_location": "berk"}',
                config=config
            )

        self.assertEqual(len(self.get_dispatched_triggers()), 0)
