def main():
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
    import network
    import machine, esp, sys
    import gc, utime
    from ntptime import time as get_ntp_time
    import ujson
    import trequests as requests
    import awsiot_sign
    from shade_controller import Shade_controller
    from setwifi import setwifi as setwifi

    if "reset_cause" in dir(machine):
        rst = machine.reset_cause()
        print('reset-cause: ', end='')
        if rst == machine.PWRON_RESET: # -- 0
            print('PWRON')
        elif rst == machine.WDT_RESET: # -- 1
            print('WDT')
        elif rst == machine.SOFT_RESET: # -- 4
            print('SOFT')
        elif rst == machine.DEEPSLEEP_RESET: # -- 5
            print('DEEPSLEEP')
        elif rst == machine.HARD_RESET: # -- 6
            print('HARD')
        # overlap of deepsleep & soft_reset: elif rst = machine.DEEPSLEEP: # -- 4

    exception_msg = None

    thing = Shade_controller()
    # LED is blinked:  after (1) reset/initialization, (2) connecting to WiFi, (3) getting NTP and (4) getting state from AWS-IOT
    thing.blink_led()

    if "sleep_type" in dir(esp):
        esp.sleep_type(esp.SLEEP_NONE) # don't shut off wifi when sleeping
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if sys.platform == 'esp32':
        cfg_info = get_cfg_info("wifi_info.txt")
        if not cfg_info:
            setwifi()
        cfg_info = get_cfg_info("wifi_info.txt")
        if not cfg_info:
            thing.goto_sleep(cause="Error: could not obtain wifi configuration")
        wlan.connect(cfg_info['SSID'], cfg_info['password'])

    connected = None
    for _ in range(20):
        connected = wlan.isconnected()
        if connected:
            break
        else:
            utime.sleep_ms(333)
    if not connected:
        setwifi()
        exception_msg = "Warning: unable to connect to WiFi; setWiFi run to get new credentials"
        thing.goto_sleep(cause=exception_msg)

    thing.blink_led() #2nd blink: after WiFi connection

    t_secs = None # number of seconds from the year 2000
    for _ in range(5):
        try:
            t_secs = get_ntp_time()
            break
        except Exception as e:
            print("Exception in get NTP: {}".format(str(e)))
    if t_secs is None:
        thing.goto_sleep(cause="Error: failed to connect to NTP server")

    thing.blink_led() #3rd blink: after getting time from NTP

    time_tuple = utime.localtime(t_secs)
    datestamp = "{0}{1:02d}{2:02d}".format(time_tuple[0],time_tuple[1],time_tuple[2])
    time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3],time_tuple[4],time_tuple[5])
    date_time = datestamp + "T" + time_now_utc + "Z"
    start_ticks = utime.ticks_ms()

    aws_info = get_cfg_info("aws_info.txt")
    if not aws_info:
        thing.goto_sleep(cause="Error: unable to obtain AWS IOT access parameters")

    request_dict = awsiot_sign.request_gen(aws_info['endpt_prefix'], thing.id, aws_info['akey'], aws_info['skey'], date_time)
    endpoint = 'https://' + request_dict["host"] + request_dict["uri"]

    try:
        r = requests.get(endpoint, headers=request_dict["headers"])
    except Exception as e:
        exception_msg = "{} -- Exception on GET request: {}".format(date_time, e)
        thing.goto_sleep(cause=exception_msg)

    # print(r.headers)
    if (r.status_code == 200):
        thing.shadow_state = r.json()
        if (('state' not in thing.shadow_state) or ('desired' not in thing.shadow_state['state'])):
            print('Invalid state recieved: '.format(thing.shadow_state))
            thing.goto_sleep(cause="{0} -- Error: Invalid shadow state recieved from AWS".format(date_time))
        thing.blink_led() #4th blink - after GET shadow state
        # print("calling process_shadow_state")
        thing.process_shadow_state()
        # print("returned from process_shadow_state")
    else:
        exception_msg = "{} -- Error on GET: code: {}  reason: {}".format(date_time, r.status_code, r.reason)
        thing.goto_sleep(cause=exception_msg)

    # check conditions: the thing can check battery metrics, environmental conditions, etc.
    thing.check_conditions(t_secs)

    if len(thing.reported_state) > 0:
        post_body = {'state' : {'reported': {} } }
        for key, value in thing.reported_state.items():
            post_body['state']['reported'][key] = value
        post_body_str = ujson.dumps(post_body)
#        print("posting: " + post_body_str)

        # == update the timestamp (shift right 10 is approx equal to divide by 1000
        elapsed_secs = utime.ticks_diff(utime.ticks_ms(), start_ticks) >> 10
        time_tuple = utime.localtime(t_secs + elapsed_secs)
        time_now_utc = "{0:02d}{1:02d}{2:02d}".format(time_tuple[3],time_tuple[4],time_tuple[5])
        date_time = datestamp + "T" + time_now_utc + "Z"

        request_dict = awsiot_sign.request_gen(aws_info['endpt_prefix'], thing.id, aws_info['akey'], aws_info['skey'], date_time, method='POST', body=post_body_str)
        gc.collect()
        print("Free mem before POST: {0}".format(gc.mem_free()))

        try:
            # not using json as data in POST to save a second encoding
            r = requests.post(endpoint, headers=request_dict["headers"], data=post_body_str)
            if r.status_code != 200:
                # print("post_reply: ", end =""); print(r.json())
                exception_msg = "{} -- Error on POST: code: {}  reason: {}".format(date_time, r.status_code, r.reason)
                thing.goto_sleep(cause=exception_msg)
        except Exception as e:
            exception_msg = "{} -- Exception on POST request: {}".format(date_time, e)
            thing.goto_sleep(cause=exception_msg)

    elapsed_msecs = utime.ticks_diff(utime.ticks_ms(), start_ticks)
    print("Main took: {0} msec. ---  Free mem before exit: {1}".format(elapsed_msecs, gc.mem_free()))
    thing.goto_sleep()


def get_cfg_info(filename):
    #TODO: get the keys from a secure store instead of the flash filesystem
    import ujson
    try:
        with open(filename) as f:
            cfg_info = ujson.load(f)
        return cfg_info
    except OSError as e:
        e_str = str(e)
        print("Exception (get_cfg_info) filename: {}   Error: {}".format(filename, e_str))
        return None
