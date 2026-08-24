#!/usr/bin/env python

import requests
from requests.exceptions import RequestException

from st2common.runners.base_action import Action

# requests has no default timeout: without these a stalled processing-service
# connection blocks forever, which also stalls the calling workflow's error
# branch and its notification.
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 60


class SubmitMiarkaJob(Action):
    """POSTs a JSON job request to the Miarka processing-service.

    Replaces the `core.local` + curl pattern for these endpoints. That pattern
    interpolated caller-supplied values into a shell command -- both inside the
    single-quoted --data body (where a literal quote terminates the argument)
    and into an unquoted URL position -- putting every parameter on the wrong
    side of a shell injection boundary. Here the request is built as structured
    data and serialized by requests, so no value is ever shell-interpreted.

    It also checks the HTTP status: `curl -s` exits 0 on 4xx/5xx, so a rejected
    submission previously looked like success and the workflow only failed
    later, obscurely, when it tried to read `link` off an error body.

    `process_host_url` must include the `https://` scheme: the api key is
    sent as a request header, and the curl this replaces used `-k` (no
    certificate validation at all), so the key was previously exposed to any
    MITM on the path. Certificate validation now defaults to on. To trust an
    internal/self-signed CA without turning validation back off, point the
    `REQUESTS_CA_BUNDLE` environment variable at the CA bundle on the st2
    host -- requests honours it, and so does ductus.poll_status further along
    the same chain.
    """

    def run(
        self,
        process_host_url,
        process_host_gateway_port,
        job_status_url,
        endpoint,
        payload,
        gateway_api_key,
        verify_ssl_cert=True,
    ):
        base_url = "{0}:{1}{2}".format(
            process_host_url, process_host_gateway_port, job_status_url
        )

        # gateway_api_key travels in a request header, so refuse to send it
        # over anything but TLS. A schemeless process_host_url would also
        # fail in requests, but with a less obvious error than this.
        if not base_url.lower().startswith("https://"):
            self.logger.error(
                "Refusing to send the gateway api key to a non-https url: %s "
                "(process_host_url must include the https:// scheme)",
                base_url,
            )
            return False, {"error": "process_host_url is not https"}

        url = "{0}{1}".format(base_url, endpoint)
        headers = {"apikey": gateway_api_key}

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                verify=verify_ssl_cert,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except RequestException as err:
            self.logger.error(
                "Failed to reach miarka processing-service at %s: %s", url, err
            )
            return False, {"error": str(err)}

        if not response.ok:
            self.logger.error(
                "Miarka processing-service returned status_code=%s for %s: %s",
                response.status_code,
                url,
                response.text,
            )
            return False, {"status_code": response.status_code, "body": response.text}

        try:
            body = response.json()
        except ValueError:
            self.logger.error(
                "Could not decode response from %s as json: %s", url, response.text
            )
            return False, {"status_code": response.status_code, "body": response.text}

        # The service returns a link to the created job; callers poll the job
        # id appended to the same base url. Built here so workflows don't have
        # to do string surgery on the response in YAQL.
        link = body.get("link")
        if not link:
            self.logger.error("Response from %s contained no 'link' field: %s", url, body)
            return False, {"status_code": response.status_code, "body": body}

        job_id = link.rstrip("/").split("/")[-1]

        return True, {
            "status_code": response.status_code,
            "body": body,
            "status_url": "{0}{1}".format(base_url, job_id),
        }
