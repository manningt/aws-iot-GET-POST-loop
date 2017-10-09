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
        """ returns a GMT timestamp to be used when generating the AWS request."""
        from ntptime import time as get_ntp_time
        timestamp = None
        if 'timestamp' in self._restored_state:
            timestamp = self._restored_state['timestamp'] + self._current_state['params']['sleep']
            self._current_state['timestamp'] = timestamp
            print("restored timestamp: {}".format(timestamp))
        else:
            for _ in range(6):
                try:
                    timestamp = get_ntp_time()
                    self._current_state['timestamp'] = timestamp
                    break
                except Exception as e:
                    print("Exception in get NTP: {}".format(str(e)))
        return timestamp

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
