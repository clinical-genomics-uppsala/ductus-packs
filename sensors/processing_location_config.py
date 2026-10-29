class ProcessingLocationConfig:
    """Looks up processing location dependent transfer settings in the pack config."""

    UPLOAD_KEYS = (
        'upload_host',
        'upload_user',
        'upload_key',
        'upload_path_prefix',
        'upload_hospital_staging_path',
        'upload_copy_method',
    )
    DOWNLOAD_KEYS = (
        'download_host',
        'download_user',
        'download_key',
        'download_path_prefix',
        'download_copy_method',
    )
    REMOTE_UPLOAD_KEYS = ('upload_host', 'upload_user', 'upload_key')
    REMOTE_DOWNLOAD_KEYS = ('download_host', 'download_user', 'download_key')

    def __init__(self, config, logger):
        """Initializes with the pack config and the calling sensor's logger."""
        self._config = config
        self._logger = logger
        self._check_remote_settings(config['transfer_settings_upload'], self.REMOTE_UPLOAD_KEYS, 'upload')
        self._check_remote_settings(config['transfer_settings_download'], self.REMOTE_DOWNLOAD_KEYS, 'download')

    def get_upload_transfer_settings(self, remote_host):
        """Returns the settings for uploading data to the given processing location."""
        self._logger.info("Read transfer settings for upload to {0}".format(remote_host))
        settings_upload = self._config['transfer_settings_upload'][remote_host]
        return self._with_all_keys(settings_upload, self.UPLOAD_KEYS)

    def get_download_transfer_settings(self, remote_host):
        """Returns the settings for downloading results from the given processing location."""
        self._logger.info("Read transfer settings for download from {0}".format(remote_host))
        settings_download = self._config['transfer_settings_download'][remote_host]
        return self._with_all_keys(settings_download, self.DOWNLOAD_KEYS)

    def _with_all_keys(self, settings, keys):
        """Returns the given keys from settings, using an empty string for missing or empty values."""
        return {key: settings.get(key) or '' for key in keys}

    def _check_remote_settings(self, locations, required_keys, direction):
        """Raises if a remote location lacks any of the keys a remote transfer needs."""
        for location, settings in locations.items():
            if settings.get('{0}_copy_method'.format(direction)) != 'remote':
                continue
            missing = [key for key in required_keys if not settings.get(key)]
            if missing:
                raise ValueError("Remote {0} location {1} is missing {2}".format(
                    direction, location, ", ".join(missing)))
