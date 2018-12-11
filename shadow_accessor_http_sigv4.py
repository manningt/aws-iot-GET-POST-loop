import logging

logger = logging.getLogger(__name__)

import gc
import ujson
import awsiot_sign
import trequests as requests

""" trequests is a modified version of urequests which provides a work-around for AWS not closing the
 socket after a request.  It uses the content-length from the headers when reading the data from the socket.
 Without the modification, the request to AWS hangs since the socket doesn't close.
 I renamed the module trequests instead of urequests in order to avoid stomping on the micropython-lib
 The code can be fetched from:
 https://github.com/manningt/micropython-lib/tree/urequest-with-content-length/urequests
"""


class ShadowAccessor:
    """ this module provides functions to do:
         - an https GET request to aws-iot to obtain a shadow.
         - an https POST request to aws-iot to update a shadow.
         - need to call connect before the get or update
    """

    def __init__(self):
        self._thing = None
        self._thing_id = None
        self._aws_iot_cfg = None
        self._aws_credentials = None

    def connect(self, thing):
        """ HTTP doesn't open a connection whereas other protocols (MQTT) do.
            This function is here so that connect can be called on any form of shadow accessor.
            The connect function in the HTTPS class obtains the parameters needed for the AWS signature.
            Returns an exception message if getting the parameters fails, otherwise returns None
        """
        self._thing = thing
        self._thing_id = thing.id
        self._aws_iot_cfg = thing.get_aws_iot_cfg()
        if not self._aws_iot_cfg:
            return "Error: unable to obtain AWS IOT access parameters"
        self._aws_credentials = thing.get_aws_credentials()
        if not self._aws_credentials:
            return "Error: unable to obtain AWS credentials"
        return None

    def get(self):
        """
        :return: a tuple consisting of an exception string and the JSON shadow
           - if an exception happens, the exception string will be valid and the shadow will be None
           - on no exception, the exception string will be None and the shadow will be JSON
        """
        exception_msg = None
        date_time = self._get_new_date_time()
        if date_time is None:
            return ("Error: failed to get current time", None)

        request_dict = awsiot_sign.request_gen(self._aws_iot_cfg['endpt_prefix'], self._thing_id, \
                                               self._aws_credentials['akey'], self._aws_credentials['skey'], \
                                               date_time, region=self._aws_iot_cfg['region'])
        endpoint = 'https://' + request_dict["host"] + request_dict["uri"]
        try:
            r = requests.get(endpoint, headers=request_dict["headers"])
        except Exception as e:
            exception_msg = "{} -- Exception on GET request: {}".format(date_time, e)
            return (exception_msg, None)

        if (r.status_code == 200):
            shadow_state_json = r.json()
            logger.debug("Received shadow:\n\t %s\n", shadow_state_json)
            if (('state' not in shadow_state_json) or ('desired' not in shadow_state_json['state'])):
                logger.error("Invalid state recieved:\n\t %s", shadow_state_json)
                exception_msg = "{0} -- Error: Invalid shadow state recieved from AWS".format(date_time)
            r.close()  # need to close first request before making second request
            return (exception_msg, shadow_state_json)
        else:
            exception_msg = "Error on GET: code: {}  reason: {}  timestamp: {}".format(r.status_code, r.reason,
                                                                                       date_time)
            return (exception_msg, None)

    def update(self, post_body_str):
        """
        :return: an exception string, which will be None if no error/exception occurs
        """
        exception_msg = None
        date_time = self._get_new_date_time()
        if date_time is None:
            return ("Error: failed to get current time")

        request_dict = awsiot_sign.request_gen(self._aws_iot_cfg['endpt_prefix'], self._thing_id, \
                                               self._aws_credentials['akey'], self._aws_credentials['skey'], \
                                               date_time, region=self._aws_iot_cfg['region'], \
                                               method='POST', body=post_body_str)
        endpoint = 'https://' + request_dict["host"] + request_dict["uri"]
        gc.collect()
        logger.debug("Free mem before POST: %d", gc.mem_free())

        try:
            # not using json as data in POST to save a second encoding
            r = requests.post(endpoint, headers=request_dict["headers"], data=post_body_str)
            if r.status_code != 200:
                logger.debug("post_reply: %s", r.json())
                exception_msg = "{} -- Error on POST: code: {}  reason: {}".format(date_time, r.status_code, r.reason)
            r.close()
            return exception_msg
        except Exception as e:
            exception_msg = "{} -- Exception on POST request: {}".format(date_time, e)
            return exception_msg

    def _get_new_date_time(self):
        time_tuple = self._thing.time()  # get the most recent timestamp
        if time_tuple is None:
            return None
        datestamp = "{0}{1:02d}{2:02d}".format(time_tuple[0], time_tuple[1], time_tuple[2])
        time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3], time_tuple[4], time_tuple[5])
        return (datestamp + "T" + time_now_utc + "Z")

    def disconnect(self):
        return
