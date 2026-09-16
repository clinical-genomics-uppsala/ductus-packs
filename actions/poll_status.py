#!/usr/bin/env python

import time
from urllib.parse import urlparse
import json
import logging

import requests

from requests.exceptions import RequestException, HTTPError

# Needs to be run in a Stackstorm virtualenv
from st2common.runners.base_action import Action


class PollStatus(Action):
    """
    Polls a given micro service URL for current status of some long running process.

    Will check the HTTP server's response to determine whether or not the process has finished
    processing. It expects a JSON field called "state", and will continue to poll the URL
    as long as "state" equals "started". If e.g. "done", "error", or "none" is received an
    error is generated and the polling stops.
    """

    def __init__(self, *args, **kwargs):
        super(PollStatus, self).__init__(*args, **kwargs)
        handler = self.logger.handlers[0]
        if "[%(asctime)s] " not in handler.formatter._fmt:
            formatter = logging.Formatter("[%(asctime)s] " + handler.formatter._fmt)
            handler.setFormatter(formatter)

    # (connect, read). Without a read timeout a single unanswered GET blocks
    # forever, and the wall-clock deadline below never gets a chance to run --
    # which is the difference between "this job took too long" and an st2
    # worker wedged until it is restarted.
    REQUEST_TIMEOUT = (5, 60)

    def query(self, url, verify_ssl_cert, api_key=None):
        try:
            headers = {"apikey": api_key} if api_key else None
            resp = requests.get(
                url,
                headers=headers,
                verify=verify_ssl_cert,
                timeout=self.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp
        except RequestException as err:
            self.logger.error(
                "An error was encountered when "
                "querying url: {0},  {1}".format(url, err)
            )
            raise err
        except HTTPError as err:
            self.logger.error(
                "An error was encountered when querying the url {}: {}".format(url, err)
            )

    def post_to_endpoint(
        self, endpoint, body, uppmax_mode, verify_ssl_cert, api_key=None
    ):
        def _rewrite_link(link):
            endpoint_parsed = urlparse(endpoint)
            # Gets the first non-empty element from the path, this lets it account
            # for multiple slashes
            first_part_of_path = next(s for s in endpoint_parsed.path.split("/") if s)
            link_parsed = urlparse(link)
            return "{}://{}/{}{}?{}".format(
                endpoint_parsed.scheme,
                endpoint_parsed.netloc,
                first_part_of_path,
                link_parsed.path,
                endpoint_parsed.query,
            )

        try:
            # Remove any values that are empty.
            headers = {"apikey": api_key} if api_key else None
            cleaned_body = {}
            if body:
                for key, value in body.items():
                    if value:
                        cleaned_body[key] = value
            response = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(cleaned_body),
                verify=verify_ssl_cert,
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            response_json = response.json()

            if uppmax_mode:
                modified_link = _rewrite_link(response_json["link"])
                self.logger.info(
                    "In uppmax mode, will rewrite link to: {}".format(modified_link)
                )
                return {"response": response_json, "url": modified_link}
            else:
                return {"response": response_json, "url": response_json["link"]}
        except HTTPError as err:
            self.logger.error(
                "An error was encountered when trying to post to url {}: {}".format(
                    endpoint, err
                )
            )
        except RequestException as err:
            self.logger.error(
                "An error was encountered when trying to "
                "post to url: {0}, {1}".format(endpoint, err)
            )
            raise err
        except KeyError as err:
            self.logger.error(
                "Could not find correct key in response json: {}".format(response_json)
            )
            raise err
        except ValueError as err:
            self.logger.error(
                "Error decoding response as json. Got status: {} and response: {}".format(
                    response.status_code, response.content
                )
            )
            raise err

    def check_status(
        self,
        url,
        sleep,
        ignore_result,
        verify_ssl_cert,
        max_retries,
        api_key=None,
        timeout_sec=None,
    ):
        """
        Query the url end-point. Can be called directly from StackStorm, or via the script cli
        :param url: to call
        :param sleep: minutes to sleep between attempts
        :param ignore_result: return 0 exit status even if polling failed (for known errors).
        :param verify_ssl_cert: Set to False to skip verifying the ssl cert when making requests
        :param max_retries: maximum number of retries
        :param timeout_sec: wall-clock seconds to keep polling before giving up.
                            None polls forever, which is the old behaviour.
        :return: (bool, dict)
        """
        retry_attempts = 0
        state = "started"
        started_at = time.time()

        def _remaining():
            if timeout_sec is None:
                return None
            return timeout_sec - (time.time() - started_at)

        def _sleep_bounded():
            """Sleep the poll interval, but never past the deadline.

            Without the cap a 3-day deadline with a 60m interval overshoots by
            up to an hour, and the timeout is reported later than it happened.
            """
            interval = sleep * 60
            remaining = _remaining()
            if remaining is not None:
                interval = min(interval, max(remaining, 0))
            if interval > 0:
                time.sleep(interval)

        while state == "started" or state == "pending" or not state:
            remaining = _remaining()
            if remaining is not None and remaining <= 0:
                elapsed = int(time.time() - started_at)
                self.logger.error(
                    "{} still reported state {} after {}s "
                    "(timeout_sec={}). Giving up polling.".format(
                        url, state or "<none>", elapsed, timeout_sec
                    )
                )
                return False, {
                    "timed_out": True,
                    "elapsed_sec": elapsed,
                    "timeout_sec": timeout_sec,
                    "last_state": state,
                    "url": url,
                }

            resp = self.query(url, verify_ssl_cert, api_key=api_key)
            json_resp = resp.json()
            state = json_resp["state"]

            if state == "started" or state == "pending":
                self.logger.info(
                    "{} returned state {}. "
                    "Sleeping {}m until retrying again...".format(url, state, sleep)
                )
                _sleep_bounded()
            elif state == "done":
                self.logger.info(
                    "{} returned state {}. "
                    "Will now stop polling the status.".format(url, state)
                )

                return True, json_resp
            elif state in ["error", "none", "cancelled"]:
                self.logger.warning(
                    "{} returned state {}. "
                    "Will now stop polling the status.".format(url, state)
                )

                if ignore_result:
                    self.logger.warning(
                        "Ignoring the failed result because of override flag."
                    )
                    return True, json_resp
                else:
                    return False, json_resp

            elif not state and retry_attempts < max_retries:
                retry_attempts += 1
                self.logger.warning(
                    "{} did not report state. "
                    "Probably due to a connection error, "
                    "will retry. Attempt {} of {}.".format(
                        url, retry_attempts, max_retries
                    )
                )
                _sleep_bounded()
            else:
                self.logger.error(
                    "{} returned state unknown state {}. "
                    "Will now stop polling the status.".format(url, state)
                )
                return False, json_resp

    def run(
        self,
        url,
        body,
        sleep,
        ignore_result,
        uppmax_mode,
        verify_ssl_cert,
        max_retries=3,
        uppmax_api_key=None,
        timeout_sec=259200,
    ):
        #start_response = self.post_to_endpoint(
        #    url, body, uppmax_mode, verify_ssl_cert, uppmax_api_key
        #)
        #status_link = start_response["url"]
        status_link = url
        status_val, status_response = self.check_status(
            status_link,
            sleep,
            ignore_result,
            verify_ssl_cert,
            max_retries,
            uppmax_api_key,
            timeout_sec,
        )
        return status_val, {
            "response_from_last_status_check": status_response,
        }
#        return status_val, {
#            "response_from_start": start_response,
#            "response_from_last_status_check": status_response,
#        }
