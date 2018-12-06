def main(thing_type='Signal', protocol='HTTPS'):
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

        # update the thing's timestamp.  This will be used when reporting condition changed based on time intervals
        time_tuple = thing.time() # different things obtain the time in different ways; needs to be GMT
        if time_tuple is None:
            thing.sleep(msg="Error: failed to get current time")
            break
        if getattr(thing, "show_progress", None) != None:
            thing.show_progress(3, 4)  # after getting time from NTP


        if protocol == 'HTTPS':
            from shadow_accessor_http_sigv4 import ShadowAccessor
        else:
            msg = format("Error: Unsupported protocol: {}", protocol)
            thing.sleep(msg)
            break

        shadow_accessor = ShadowAccessor()
        status_msg = shadow_accessor.connect(thing)
        if status_msg != None:
            thing.sleep(status_msg)
            break

        status_msg, shadow_state_json = shadow_accessor.get()
        if status_msg != None:
            thing.sleep(status_msg)
            break
        if getattr(thing, "show_progress", None) != None:
            thing.show_progress(4, 4)  # after GET shadow state
        thing.shadow_state = shadow_state_json

        reported_state = thing.reported_state
        if len(reported_state) > 0:
            post_body = {'state': {'reported': {}}}
            for key, value in reported_state.items():
                post_body['state']['reported'][key] = value
            post_body_str = ujson.dumps(post_body)
            logger.debug("Posting: %s", post_body_str)

            status_msg = shadow_accessor.update(post_body_str)
            if status_msg != None:
                thing.sleep(status_msg)
                break

        elapsed_msecs = utime.ticks_diff(utime.ticks_ms(), start_ticks)
        logger.info("Main took: %d msec. ---  Free mem before sleep: %d", elapsed_msecs, gc.mem_free())
        thing.sleep()
