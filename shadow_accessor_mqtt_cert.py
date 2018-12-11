import logging
import gc
import ujson
from utime import sleep_ms
from umqtt.simple import MQTTClient, MQTTException

logger = logging.getLogger(__name__)


class ShadowAccessor:
    """ this module provides functions to do:
         - open and close and MQTT connection
         - publish a MQTT GET to aws-iot to obtain a shadow.
         - publish a MQTT UPDATE to aws-iot to update a shadow.
         - need to call connect before the get or update
    """

    def __init__(self):
        self._thing = None
        self._thing_id = None
        self._aws_iot_cfg = None
        self._aws_private_key = None
        self._aws_certificate = None
        self.msg_rcvd = None
        self.topic_rcvd = None
        self._client = None
        self._base_topic = None
        self._keepalive = 4000

    def _callback(self, topic, msg):
        logger.debug("In callback:\n\tTopic: %s\n\t%s", topic, msg)
        self.topic_rcvd = topic
        self.msg_rcvd = msg

    def connect(self, thing):
        """ Opens a MQTT connection
            Returns an exception message if getting the connection fails, otherwise returns None
        """
        self._thing = thing
        self._thing_id = thing.id
        self._aws_iot_cfg = thing.get_aws_iot_cfg()
        if not self._aws_iot_cfg:
            return "Error: unable to obtain AWS IOT access parameters"
        self._aws_private_key = thing.get_private_key()
        if not self._aws_private_key:
            return "Error: unable to obtain AWS private key for device"
        self._aws_certificate = thing.get_certificate()
        if not self._aws_certificate:
            return "Error: unable to obtain AWS certificate for device"

        self._aws_server = self._aws_iot_cfg['endpt_prefix'] + ".iot." + self._aws_iot_cfg['region'] + ".amazonaws.com"
        ssl_parms = {"key": self._aws_private_key, "cert": self._aws_certificate, "server_side": False}
        # logger.debug("ssl_parms: %s\n", str(ssl_parms))

        self._client = MQTTClient(client_id=self._thing_id, server=self._aws_server, port=8883,
                                  keepalive=self._keepalive, ssl=True, \
                                  ssl_params=ssl_parms)
        self._client.set_callback(self._callback)

        logger.debug("MQTT connecting to: %s", self._aws_server)
        try:
            client_status = self._client.connect()
        except MQTTException as e:
            exception_msg = "Exception on MQTT connect: {}".format(e)
            return exception_msg

        self._base_topic = "$aws/things/" + self._thing_id + "/shadow"
        self.subscribe("/get/accepted")
        self.subscribe("/update/accepted")
        return None

    def subscribe(self, shadow_topic):
        logger.debug("subscribe to: %s", self._base_topic + shadow_topic)
        self._client.subscribe(self._base_topic + shadow_topic)

    def publish(self, shadow_topic, msg=""):
        logger.debug("publish topic: %s -- msg: '%s'", self._base_topic + shadow_topic, msg)
        self._client.publish(self._base_topic + shadow_topic, msg)

    def get(self):
        """
        :return: a tuple consisting of an exception string and the JSON shadow
           - if an exception happens, the exception string will be valid and the shadow will be None
           - on no exception, the exception string will be None and the shadow will be JSON
        """
        exception_msg = None
        shadow_state_json = None
        self.publish("/get")

        for i in range(18):
            sleep_ms(333)
            self._client.check_msg()
            # TODO: check topic is get/accepted
            if self.msg_rcvd != None:
                logger.debug("shadow state received on get: %s", self.msg_rcvd)
                try:
                    shadow_state_json = ujson.loads(self.msg_rcvd)
                except Exception as e:
                    exception_msg = "Shadow received was not json format: {}".format(e)
                break

        if self.msg_rcvd == None:
            logger.warning("Did not get response to publish of shadow/get on check_msg attempt: %d", (i + 1))
            exception_msg = "Did not get response to publish of shadow/get on check_msg attempt: {}".format(i + 1)
        return (exception_msg, shadow_state_json)

    def update(self, state):
        """
        :return: an exception string, which will be None if no error/exception occurs
        """
        exception_msg = None
        if state == None:
            return "Call to update error: state is None"
        self.publish("/update", msg=state)

        for i in range(18):
            sleep_ms(333)
            # if i == 0:
            #     sleep_ms(1000)
            self._client.check_msg()
            if self.topic_rcvd != None:
                break
        if self.msg_rcvd == None:
            logger.warning("Did not get response to publish of shadow/update on check_msg attempt: %d", (i + 1))
            exception_msg = "Did not get response to publish of shadow/get on check_msg attempt: {}".format(i + 1)
        return exception_msg

    def disconnect(self):
        self._client.disconnect()
