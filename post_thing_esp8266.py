from base_thing import BaseThing
class PostThing(BaseThing):
    """ On wake-up, includes 'powerApplied' = 1 in reported state that is POSTed

        The use case is power-on monitoring - the updated reported state & timestamp can be written to a database
        that is analyzed to determine times between power-on cycles
        """
    def __init__(self):
        import machine
        # instantiate RTC which is used as a persistent store during the super--init
        self.rtc = machine.RTC()
        super().__init__()
        # if a parameter is not restored from persistence, then add it with a default value
        if 'powerApplied' not in self._current_state['params']:
            self._current_state['params']['powerApplied'] = 0
        self._start_ticks = None

    @property
    def id(self):
        """ returns a thing-shadow ID to be used when generating the AWS request for shadow state """
        from machine import unique_id
        id_reversed = unique_id()
        id_binary = [id_reversed[n] for n in range(len(id_reversed) - 1, -1, -1)]
        my_id = "ESP-" + "".join("{:02x}".format(x) for x in id_binary)
        return my_id

    def connect(self):
        """ activates the wlan and polls to see if connected
            returns a tuple:
              - a boolean to indicate successful connection or not
              - a msg to display if connection failed
        """
        import esp, network
        from utime import sleep_ms

        if "sleep_type" in dir(esp):
            esp.sleep_type(esp.SLEEP_NONE)  # don't shut off wifi when sleeping
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        connected = False
        for _ in range(20):
            connected = wlan.isconnected()
            if connected:
                return True, None
            else:
                sleep_ms(333)
        if not connected:
            from setwifi import setwifi as setwifi
            setwifi()
            return False, "Warning: unable to connect to WiFi; setWiFi run to get new credentials"

    def time(self):
        """ returns a tuple date/times to be used when generating the AWS request."""
        from ntptime import time as get_ntp_time
        import utime

        # The shadow timestamp is from 1970-01-01 vs micropython is from 2000-01-01
        SECONDS_BETWEEN_1970_2000 = 946684800
        time_tuple = None
        if self._start_ticks is None:
            for _ in range(5):
                utime.sleep_ms(3000)
                try:
                    self._timestamp = get_ntp_time()
                    break
                except Exception as e:
                    print("Exception in get NTP: {}".format(str(e)))

            if self._timestamp is None:
                print("Error: failed to get time from NTP")
            elif type(self._timestamp).__name__=='int':
                try:
                    time_tuple = utime.localtime(self._timestamp)
                    # adjust the stored timestamp used for reporting conditions based on an interval
                    self._start_ticks = utime.ticks_ms()
                    self._timestamp += SECONDS_BETWEEN_1970_2000
                except:
                    print("Error: Exception on timestamp conversion; timestamp: {}".format(self._timestamp))
            else:
                print("NTP timestamp not an int: {}".format(self._timestamp))
        else:
            # get updated time by adding elapsed time to existing timestamp
            #     - shift right 10 is approx equal to divide by 1000 in order to get seconds
            elapsed_secs = utime.ticks_diff(utime.ticks_ms(), self._start_ticks) >> 10
            time_tuple = utime.localtime(self._timestamp - SECONDS_BETWEEN_1970_2000 + elapsed_secs)
        return time_tuple

    def sleep(self,msg=None):
        """ never returns; puts the ESP into deep sleep."""
        import machine
        from sys import exit
        from utime import sleep_ms

        RESET_TIMEOUT = 120000 # 3 minutes in milliseconds

        LOG_FILENAME = "./log.txt"
        if msg is not None:
            print(msg)
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write(msg + "\n")

        if self._current_state['params']['sleep'] < 1:
            # exit: stop the infinite loop of main & deep-sleep
            print("Staying awake due to sleep parameter < 1.")
            tim = machine.Timer(-1)
            tim.init(period=RESET_TIMEOUT, mode=machine.Timer.ONE_SHOT, callback=lambda t: machine.reset())
            exit(0)

        print("Going to sleep for {0} seconds.".format(self._current_state['params']['sleep']))
        sleep_ms(self._current_state['params']['sleep'] << 10)
        machine.reset()

    def _persist_state(self):
        import ujson
        self.rtc.memory(ujson.dumps(self._current_state))

    def _restore_state(self):
        import ujson
        try:
            tmp = ujson.loads(self.rtc.memory())
            print("restored state: {}".format(tmp))
            if type(tmp) is not dict or 'params' not in tmp:
                print("Warning (restore_state): RTC memory did not have parameters")
                tmp = {}
        except:
            print("Warning (restore_state): RTC memory was not JSON")
            tmp = {}
        return tmp

    # @property
    def _shadow_state_get(self):
        return super()._shadow_state_get()

    # @shadow_state.setter
    def _shadow_state_set(self, shadow_state):
        self._current_state['params']['powerApplied'] += 1
        self._reported_state['powerApplied'] = self._current_state['params']['powerApplied']
        super()._shadow_state_set(shadow_state)

    shadow_state = property(_shadow_state_get, _shadow_state_set)
