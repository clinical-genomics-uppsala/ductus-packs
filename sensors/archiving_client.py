import requests
import jsonpickle


class ArchivingClient():
    """Pulls data from processing api. If there are issues with connecting
       to any of the hosts, it will be logged but processing will continue""" 

    def __init__(self, hosts, logger):
        """Initializes the client with a list of hosts""" 
        logger.info("__init__ ArchivingClient")
        if not isinstance(hosts, list):
            self._hosts = [hosts]
        else:
            self._hosts = hosts
        self._logger = logger

    def next_ready(self):
        """Pulls analysis from processing api..
           Hosts are queried in the order specified in the constructor."""
        for host in self._hosts:
            # TODO: Add packs id to log in a generic way
            self._logger.info("Querying {0}".format(host))
            url = host
            try:
                resp = requests.get(url)
                if resp.status_code not in [200, 204]:
                    self._logger.error("ArchivingClient: Got status_code={0} from "
                                       "endpoint {1}".format(resp.status_code, url))
                elif resp.status_code == 204:
                    self._logger.debug("ArchivingClient: No new sequence runs from api call to {0}.".format(url))
                else:
                    json = resp.text
                    self._logger.debug("ArchivingClient: Successful call to {0}. {1}.".format(url, json))
                    result = dict()
                    result['response'] = jsonpickle.decode(json)
                    result['requesturl'] = url
                    return result 
            except requests.exceptions.ConnectionError:
               self._logger.error("ArchivingClient: Not able to connect to host {0}".format(host))
        return None