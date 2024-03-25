import requests
from requests.auth import HTTPBasicAuth
import jsonpickle


class ProcessingApiClient():
    """Pulls data from processing api. If there are issues with connecting
       to any of the hosts, it will be logged but processing will continue""" 

    def __init__(self, hosts, api_key, logger):
        """Initializes the client with a list of hosts""" 
        logger.info("__init__ ProcessingApiClient")
        if not isinstance(hosts, list):
            self._hosts = [hosts]
        else:
            self._hosts = hosts
        self.api_key = f"Api-Key {api_key}"
        self._logger = logger

    def next_ready(self):
        """Pulls analysis from processing api..
           Hosts are queried in the order specified in the constructor."""
        for host in self._hosts:
            # TODO: Add packs id to log in a generic way
            self._logger.info("Querying {0}".format(host))
            url = host
            try:
                resp = requests.get(url, headers={"Authorization": self.api_key})
                if resp.status_code != 200:
                    self._logger.error("ProcessingApiClient: Got status_code={0} from "
                                       "endpoint {1}".format(resp.status_code, url))
                else:
                    json = resp.text
                    self._logger.debug("ProcessingApiClient: Successful call to {0}. {1}.".format(url, json))
                    result = dict()
                    result['response'] = jsonpickle.decode(json)
                    result['requesturl'] = url
                    return result 
            except requests.exceptions.ConnectionError:
               self._logger.error("ProcessingApiClient: Not able to connect to host {0}".format(host))
        return None