#!/usr/bin/env python

import requests
from requests.exceptions import RequestException

from st2common.runners.base_action import Action


class UploadPacbioSequencerun(Action):
    """Registers a completed PacBio SMRT Link run with the processing api.

    Unlike the Illumina upload (a multipart file upload of SampleSheet.csv +
    RunInfo.xml done via curl in an Orquesta workflow), a PacBio run has no
    such files -- the payload is the run/collection JSON from SMRT Link, which
    needs to be posted as a real JSON body. That nested structure isn't safe
    to interpolate into a shell command, so this is a python-script action
    instead of core.local, matching the pattern already used by
    poll_status.py for JSON bodies.
    """

    def _get_ccs_execution_mode(self, collections):
        for collection in collections:
            mode = collection.get("ccsExecutionMode")
            if mode:
                return mode
        return None

    def run(
        self,
        run_uuid,
        run_name,
        collection_paths,
        collections,
        requires_demultiplex,
        processing_api_service_url,
        processing_api_sequence_run_upload_pacbio_url,
        processing_api_access_key,
    ):
        url = processing_api_service_url + processing_api_sequence_run_upload_pacbio_url
        payload = {
            "run_uuid": run_uuid,
            "run_name": run_name,
            "ccs_execution_mode": self._get_ccs_execution_mode(collections),
            "requires_demultiplex": requires_demultiplex,
            "collection_paths": collection_paths,
            "collections": collections,
        }
        headers = {"Authorization": "Api-Key {}".format(processing_api_access_key)}

        try:
            response = requests.post(url, json=payload, headers=headers)
        except RequestException as err:
            self.logger.error("Failed to reach processing api at %s: %s", url, err)
            return False, {"error": str(err)}

        if response.status_code != 201:
            self.logger.error(
                "Processing api returned status_code=%s for run %s: %s",
                response.status_code,
                run_name,
                response.text,
            )
            return False, {"status_code": response.status_code, "body": response.text}

        return True, {"status_code": response.status_code, "body": response.json()}
