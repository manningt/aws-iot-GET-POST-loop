def main(thing_type='Signal'):
    """ This function uses the AWS-IOT REST API to GET shadow state and POST state updates
        Refer to: http://docs.aws.amazon.com/iot/latest/developerguide/iot-thing-shadows.html
        This function is meant to be called after the processor wakes up.
        There is no return: the function calls thing.go_to_sleep() after any failure or when the function ends
        It does the following steps:
            - instances a 'thing' which has a state dictionary in the AWS-IOT shadow format
            - gets the current time from an NTP server in 32-bit format.  NOTE: it will fail past 2038.  Not relying on an on-board RTC
            - gets the shadow state
            - tells the thing to process the state received from AWS - this is where the device does any action specified by a state change
            - if the reported state dictionary is not empty, POST it to AWS
            
        The AWS access & secret key and endpoint ID are obtained via the function get_aws_info, which currently reads a file.
        TODO: change get_aws_info to get the keys from encrypted storage, like: http://www.microchip.com/wwwproducts/en/ATECC508A
        currently urequest function does not verify certificates
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

        thing = Thing()

        """ show_progress is an device specific feature.
            The device can blink an LED, print a statement or show a progress bar
            show_progress is called after: 
                (1) reset/initialization, (2) connecting to WiFi, (3) getting the time and (4) getting state from AWS-IOT
        """
        if 'show_progress' in dir(thing): thing.show_progress(1, 4)  # after initialization

        connected = thing.connect()
        if not connected[0]:
            thing.sleep(msg=connected[1])
            break

        if 'show_progress' in dir(thing): thing.show_progress(2, 4)  # after connected to WiFi

        t_secs = thing.time() # seconds from year 2000; different things obtain the time in different ways
        if t_secs is None:
            thing.sleep(msg="Error: failed to get current time")
            break
        if 'show_progress' in dir(thing): thing.show_progress(3, 4)  # after getting time from NTP

        try:
            time_tuple = utime.localtime(t_secs)
        except:
            thing.sleep(msg="Error: Exception on timestamp conversion; timestamp: {:x}".format(t_secs))
            break

        datestamp = "{0}{1:02d}{2:02d}".format(time_tuple[0], time_tuple[1], time_tuple[2])
        time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3], time_tuple[4], time_tuple[5])
        date_time = datestamp + "T" + time_now_utc + "Z"
        start_ticks = utime.ticks_ms()

        aws_info = get_cfg_info("aws_info.txt")
        if not aws_info:
            thing.sleep(msg="Error: unable to obtain AWS IOT access parameters")
            break

        request_dict = awsiot_sign.request_gen(aws_info['endpt_prefix'], thing.id, \
                                               aws_info['akey'], aws_info['skey'], date_time, region=aws_info['region'])
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
                print('Invalid state recieved: '.format(shadow_state_json))
                thing.sleep(msg="{0} -- Error: Invalid shadow state recieved from AWS".format(date_time))
                break
            else:
                thing.shadow_state = shadow_state_json
                if 'show_progress' in dir(thing): thing.show_progress(4, 4)  # after GET shadow state
        else:
            exception_msg = "Error on GET: code: {}  reason: {}  timestamp: {}".format(r.status_code, r.reason, date_time)
            thing.sleep(msg=exception_msg)
            break

        # need to close first request before making second request
        r.close()
        if len(thing.reported_state) > 0:
            post_body = {'state': {'reported': {}}}
            for key, value in thing.reported_state.items():
                post_body['state']['reported'][key] = value
            post_body_str = ujson.dumps(post_body)
            #        print("posting: " + post_body_str)

            # == update the timestamp (shift right 10 is approx equal to divide by 1000
            elapsed_secs = utime.ticks_diff(utime.ticks_ms(), start_ticks) >> 10
            time_tuple = utime.localtime(t_secs + elapsed_secs)
            time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3], time_tuple[4], time_tuple[5])
            date_time = datestamp + "T" + time_now_utc + "Z"

            request_dict = awsiot_sign.request_gen(aws_info['endpt_prefix'], thing.id, aws_info['akey'],
                                                   aws_info['skey'], date_time, method='POST',
                                                   region=aws_info['region'], body=post_body_str)
            # print("Before gc.collect.", )
            gc.collect()
            print("Free mem before POST: {0}".format(gc.mem_free()))

            try:
                # not using json as data in POST to save a second encoding
                r = requests.post(endpoint, headers=request_dict["headers"], data=post_body_str)
                if r.status_code != 200:
                    # print("post_reply: ", end =""); print(r.json())
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
        print("Main took: {0} msec. ---  Free mem before exit: {1}".format(elapsed_msecs, gc.mem_free()))
        thing.sleep()


def get_cfg_info(filename):
    # TODO: get the keys from a secure store instead of the flash filesystem
    import ujson
    try:
        with open(filename) as f:
            cfg_info = ujson.load(f)
        return cfg_info
    except OSError as e:
        e_str = str(e)
        print("Exception (get_cfg_info) filename: {}   Error: {}".format(filename, e_str))
        return None
