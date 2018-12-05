def main(thing_type='Signal'):
    """ This function uses the AWS-IOT REST API to GET shadow state and POST state updates
        Refer to: http://docs.aws.amazon.com/iot/latest/developerguide/iot-thing-shadows.html
        This function is meant to be called after the processor wakes up.
        There is no return: the function calls thing.go_to_sleep() after any failure or when the function ends
        It does the following steps:
            - instances a 'thing' which has a state dictionary in the AWS-IOT shadow format
            - gets the current time from an NTP server in 32-bit format.
                NOTE: it will fail past 2038.  Not relying on an on-board RTC
            - gets the shadow state
            - tells the thing to process the state received from AWS;
                this is where the device does any action specified by a state change
            - if the reported state dictionary is not empty, POST it to AWS

        The AWS access & secret key and endpoint ID are obtained via the function get_aws_info,
        which currently reads a file.  The functions to get credentials or certificates can be overloaded to obtain
        them from device specific secure/encrypted storage, like http://www.microchip.com/wwwproducts/en/ATECC508A
        """
    import gc
    import utime
    import ujson
    import awsiot_sign
    """ trequests is a modified version of urequests which provides a work-around for AWS not closing the 
        socket after a request.  It uses the content-length from the headers when reading the data from the socket.
        Without the modification, the request to AWS hangs since the socket doesn't close.
        I renamed the module trequests instead of urequests in order to avoid stomping on the micropython-lib
        The code can be fetched from: 
        https://github.com/manningt/micropython-lib/tree/urequest-with-content-length/urequests
    """
    import trequests as requests
    import logging
    logger = logging.getLogger(__name__)

    while True:
        if 'thing' in locals():
            del thing

        if thing_type == 'Signal':
            from sys import platform
            if platform.startswith('esp'):
                from signal_thing_esp8266 import SignalThing as Thing
            else:
                from signal_thing_unix import SignalThing as Thing
        elif thing_type == 'Post':
            from post_thing_esp8266 import PostThing as Thing
        elif thing_type == 'Shade':
            from shade_controller import ShadeController as Thing
        try:
            logger.debug("Using Thing.module: %s", Thing.__module__)
        except Exception as e:
            print("Incorrect logging config: %s", e)
            import sys
            sys.exit(1)


        start_ticks = utime.ticks_ms()
        thing = Thing()

        """ show_progress is an device specific feature.
            The device can blink an LED, print a statement or show a progress bar
            show_progress is called after: 
                (1) reset/initialization, (2) connecting to an IP network,
                (3) getting the time and (4) getting state from AWS-IOT
        """
        if getattr(thing, "show_progress", None) != None:
            thing.show_progress(1, 4)  # after initialization

        # connect to IP network
        connected = thing.connect()
        if not connected[0]:
            thing.sleep(msg=connected[1])
            break
        if getattr(thing, "show_progress", None) != None:
            thing.show_progress(2, 4)  # after connected to IP

        time_tuple = thing.time() # different things obtain the time in different ways; needs to be GMT
        if time_tuple is None:
            thing.sleep(msg="Error: failed to get current time")
            break
        if getattr(thing, "show_progress", None) != None:
            thing.show_progress(3, 4)  # after getting time from NTP

        datestamp = "{0}{1:02d}{2:02d}".format(time_tuple[0], time_tuple[1], time_tuple[2])
        time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3], time_tuple[4], time_tuple[5])
        date_time = datestamp + "T" + time_now_utc + "Z"

        aws_iot_cfg = thing.get_aws_iot_cfg()
        if not aws_iot_cfg:
            thing.sleep(msg="Error: unable to obtain AWS IOT access parameters")
            break
        aws_credentials = thing.get_aws_credentials()
        if not aws_iot_cfg:
            thing.sleep(msg="Error: unable to obtain AWS credentials")
            break

        request_dict = awsiot_sign.request_gen(aws_iot_cfg['endpt_prefix'], thing.id, \
                                               aws_credentials['akey'], aws_credentials['skey'], date_time, region=aws_iot_cfg['region'])
        endpoint = 'https://' + request_dict["host"] + request_dict["uri"]
        try:
            r = requests.get(endpoint, headers=request_dict["headers"])
        except Exception as e:
            exception_msg = "{} -- Exception on GET request: {}".format(date_time, e)
            thing.sleep(msg=exception_msg)
            break

        if (r.status_code == 200):
            shadow_state_json = r.json()
            if (('state' not in shadow_state_json) or ('desired' not in shadow_state_json['state'])):
                logger.error("Invalid state recieved:\n %s\n", shadow_state_json)
                thing.sleep(msg="{0} -- Error: Invalid shadow state recieved from AWS".format(date_time))
                break
            else:
                thing.shadow_state = shadow_state_json
                if getattr(thing, "show_progress", None) != None:
                    thing.show_progress(4, 4)  # after GET shadow state
        else:
            exception_msg = "Error on GET: code: {}  reason: {}  timestamp: {}".format(r.status_code, r.reason, date_time)
            thing.sleep(msg=exception_msg)
            break

        r.close()  # need to close first request before making second request
        reported_state = thing.reported_state
        if len(reported_state) > 0:
            post_body = {'state': {'reported': {}}}
            for key, value in reported_state.items():
                post_body['state']['reported'][key] = value
            post_body_str = ujson.dumps(post_body)
            logger.debug("Posting: %s", post_body_str)

            # == make an updated timestamp
            time_tuple = thing.time()
            datestamp = "{0}{1:02d}{2:02d}".format(time_tuple[0], time_tuple[1], time_tuple[2])
            time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3], time_tuple[4], time_tuple[5])
            date_time = datestamp + "T" + time_now_utc + "Z"

            request_dict = awsiot_sign.request_gen(aws_iot_cfg['endpt_prefix'], thing.id, aws_credentials['akey'],
                                                   aws_credentials['skey'], date_time, method='POST',
                                                   region=aws_iot_cfg['region'], body=post_body_str)
            gc.collect()
            logger.debug("Free mem before POST: %d", gc.mem_free())
            try:
                # not using json as data in POST to save a second encoding
                r = requests.post(endpoint, headers=request_dict["headers"], data=post_body_str)
                if r.status_code != 200:
                    logger.error("On Update; reply: \n%s\n", r.json())
                    exception_msg = "{} -- Error on POST: code: {}  reason: {}".format(date_time, r.status_code,
                                                                                       r.reason)
                    r.close()
                    thing.sleep(msg=exception_msg)
                    break
            except Exception as e:
                exception_msg = "{} -- Exception on POST request: {}".format(date_time, e)
                r.close()
                thing.sleep(msg=exception_msg)
                break

        elapsed_msecs = utime.ticks_diff(utime.ticks_ms(), start_ticks)
        logger.info("Main took: %d msec. ---  Free mem before sleep: %d", elapsed_msecs, gc.mem_free())
        thing.sleep()
