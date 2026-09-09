import base64
from datetime import datetime, timezone

import requests

requests.packages.urllib3.disable_warnings()

CLIENT_KEY = "6NjRXBcFfLZOwHc0Xlidiz4ywcsa"
CLIENT_SECRET = "KMLz5g7fbmx8RVFKKdu0NOrJic4a"
TOKEN_EXPIRY_SECONDS = 7200
TOKEN_REFRESH_MARGIN = 600   # re-auth 10 min before expiry


class SMRTClient:
    """Client for the SMRT Link REST API.

    ssl_verify defaults to True: get_token() sends the username and password
    in the request body, so an unverified connection exposes real user
    credentials, not just a token. A SMRT Link install using its default
    self-signed certificate needs REQUESTS_CA_BUNDLE pointed at that cert on
    the st2 host -- prefer that over passing ssl_verify=False, which is left
    available only for local development against a throwaway server.
    """

    def __init__(self, base_url, username, password, ssl_verify=True):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.ssl_verify = ssl_verify
        self._token = None
        self._token_acquired_at = None

    def get_token(self):
        # SECRET before KEY, which inverts RFC 6749's client_id:client_secret.
        # This is not a bug: PacBio's own API guide (PN 103-720-500, Aug 2025)
        # documents a literal Basic header that base64-decodes to
        # "<secret>:<key>", and its reference Python client does
        # ":".join([secret, consumer_key]). Swapping these yields 401.
        credentials = base64.b64encode(
            f"{CLIENT_SECRET}:{CLIENT_KEY}".encode()
        ).decode("utf-8")
        payload = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "scope": "openid run-design run-qc analysis sample-setup data-management userinfo",
        }
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = requests.post(
            f"{self.base_url}/token",
            data=payload,
            headers=headers,
            verify=self.ssl_verify,
            timeout=30,
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        self._token_acquired_at = datetime.now(timezone.utc)
        return self._token

    def refresh_if_needed(self):
        if self._token is None:
            return self.get_token()
        age = (datetime.now(timezone.utc) - self._token_acquired_at).total_seconds()
        if age > (TOKEN_EXPIRY_SECONDS - TOKEN_REFRESH_MARGIN):
            return self.get_token()
        return self._token

    def get(self, endpoint, params=None):
        """GET a SMRT Link endpoint.

        params is passed to requests rather than concatenated onto endpoint,
        so callers never have to url-encode a value themselves -- the child
        dataset lookup passes a uuid this way.
        """
        self.refresh_if_needed()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-type": "application/json",
        }
        r = requests.get(
            f"{self.base_url}/SMRTLink/1.0.0{endpoint}",
            headers=headers,
            params=params,
            verify=self.ssl_verify,
            timeout=30,
        )
        if r.status_code == 401:
            # force re-auth and retry once
            self.get_token()
            headers["Authorization"] = f"Bearer {self._token}"
            r = requests.get(
                f"{self.base_url}/SMRTLink/1.0.0{endpoint}",
                headers=headers,
                params=params,
                verify=self.ssl_verify,
                timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def post(self, endpoint, payload):
        self.refresh_if_needed()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-type": "application/json",
        }
        r = requests.post(
            f"{self.base_url}/SMRTLink/1.0.0{endpoint}",
            json=payload,
            headers=headers,
            verify=self.ssl_verify,
            timeout=30,
        )
        r.raise_for_status()
        return r

    # ------------------------------------------------------------------
    # Run / collection / dataset resolution
    #
    # Endpoint shapes below were checked against the SMRT Link swagger spec
    # (revio_docs/smrtlink_swagger.json in the st2_smrtlink repo), not
    # guessed: /smrt-link/runs/{runId}, .../collections,
    # /smrt-link/datasets/ccsreads/{datasetId},
    # /smrt-link/datasets/{datasetType} (which documents parentUuid and
    # numChildren as query params) and
    # /smrt-link/datasets/{datasetType}/{datasetId}/details all exist there.
    # ------------------------------------------------------------------

    def get_run(self, run_uuid):
        return self.get(f"/smrt-link/runs/{run_uuid}")

    def get_run_collections(self, run_uuid):
        return self.get(f"/smrt-link/runs/{run_uuid}/collections")

    def get_collection_barcodes(self, run_uuid, collection_uuid):
        return self.get(
            f"/smrt-link/runs/{run_uuid}/collections/{collection_uuid}/barcodes"
        )

    def get_ccsread(self, dataset_id):
        """One ConsensusReadSet by uuid.

        Note the `path` on the response is the .consensusreadset.xml, NOT the
        BAM -- use get_dataset_bam_paths() for that.
        """
        return self.get(f"/smrt-link/datasets/ccsreads/{dataset_id}")

    def get_child_ccsreads(self, parent_uuid):
        """The ConsensusReadSets produced by demultiplexing parent_uuid.

        SMRT Link's own outer (SMRTbell barcode) demux writes one child
        ConsensusReadSet per barcode. parentUuid is a documented query
        parameter on /smrt-link/datasets/{datasetType}; the response is a
        list of ConsensusReadDetails, each carrying parentUuid,
        dnaBarcodeName, numChildren, path and uuid.

        ASSUMPTION not yet confirmed against a live server: that filtering by
        parentUuid returns only direct children and an empty list (rather
        than an error) when there are none. Marked here the same way the
        secret/key inversion in get_token() is -- if a real response
        disagrees, this is the place to fix it.
        """
        return self.get("/smrt-link/datasets/ccsreads", params={"parentUuid": parent_uuid})

    def get_dataset_details(self, dataset_id, dataset_type="ccsreads"):
        """The DataSet XML for a dataset, rendered as JSON."""
        return self.get(f"/smrt-link/datasets/{dataset_type}/{dataset_id}/details")

    def get_dataset_bam_paths(self, dataset_id, dataset_type="ccsreads"):
        """Every BAM listed in a dataset's ExternalResources, in order.

        Reads the resource list rather than globbing the dataset directory:
        a glob picks up the .pbi sidecars, and on older layouts also scraps
        BAMs, neither of which is the HiFi read file.

        The details response is XML-turned-JSON, so the exact nesting and
        capitalisation of ExternalResources/ResourceId is not something we
        can pin down without a live server. Rather than hardcode a path into
        that structure, walk it and collect anything that looks like a
        resource id ending in .bam.
        """
        details = self.get_dataset_details(dataset_id, dataset_type=dataset_type)
        return _collect_bam_resource_ids(details)


    @classmethod
    def from_st2(cls, sensor_or_action):
        """Convenience constructor — pulls config and credentials from ST2."""
        config = sensor_or_action.config
        # Actions expose action_service, sensors expose _sensor_service --
        # this classmethod isn't called anywhere in the pack today, but
        # falls back correctly for either caller type.
        kv = getattr(sensor_or_action, "action_service", None) or sensor_or_action._sensor_service
        # local=False reads the bare global key `st2 key set` writes,
        # not the namespaced default.
        return cls(
            base_url=config.get("base_url"),
            username=kv.get_value("smrtlink.username", local=False, decrypt=True),
            password=kv.get_value("smrtlink.password", local=False, decrypt=True),
            ssl_verify=config.get("ssl_verify", True),
        )


def _collect_bam_resource_ids(node, found=None):
    """Depth-first walk collecting resource-id-ish strings ending in .bam.

    Deliberately shape-tolerant (see get_dataset_bam_paths). Only keys whose
    name looks like a resource id are considered, so a stray description or
    comment mentioning a bam filename is not mistaken for the read file.
    De-duplicates while preserving first-seen order, because the same
    resource can appear under both an ExternalResource and its FileIndices.
    """
    if found is None:
        found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                isinstance(value, str)
                and key.lower() in ("resourceid", "resource_id")
                and value.endswith(".bam")
            ):
                if value not in found:
                    found.append(value)
            else:
                _collect_bam_resource_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_bam_resource_ids(item, found)
    return found
