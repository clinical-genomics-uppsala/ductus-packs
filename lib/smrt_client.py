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

    def get(self, endpoint):
        self.refresh_if_needed()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-type": "application/json",
        }
        r = requests.get(
            f"{self.base_url}/SMRTLink/1.0.0{endpoint}",
            headers=headers,
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
