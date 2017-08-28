from base_thing import BaseThing
class ShadeController(BaseThing):

    def __init__(self):
        import machine
        if "reset_cause" in dir(machine):
            rst = machine.reset_cause()
            print('reset-cause: ', end='')
            if rst == machine.PWRON_RESET:  # -- 0
                print('PWRON')
            elif rst == machine.WDT_RESET:  # -- 1
                print('WDT')
            elif rst == machine.SOFT_RESET:  # -- 4
                print('SOFT')
            elif rst == machine.DEEPSLEEP_RESET:  # -- 5
                print('DEEPSLEEP')
            elif rst == machine.HARD_RESET:  # -- 6
                print('HARD')
                # overlap of deepsleep & soft_reset: elif rst = machine.DEEPSLEEP: # -- 4

        self.rtc = None
        if "RTC" in dir(machine):
            self.rtc = machine.RTC()

        super().__init__()
        # if a parameter is not restored from persistence, then add it with a default value
        if 'position' not in self._current_state['params']:
            self._current_state['params']['position'] = "unknown"
        self._operations['position'] = self._position
        # add tests to dictionary of available test operations
        self._test_operations['current'] = self._test_current_sensor
        self._test_operations['motor'] = self._test_motor
        self._test_operations['set-position'] = self._set_position

        self.timestamp = None  # will hold number of seconds from the year 2000

        # the following are the GPIO numbers
        self.PIN_LED = 2
        self.PIN_POWER_ENABLE = 14      # D5 on the nodeMCU board
        # self.PIN_MOTOR_ENABLE1 = 12     #D6
        # self.PIN_MOTOR_ENABLE2 = 13     #D7
        self.PIN_MOTOR_ENABLE1 = 15     #D8
        self.PIN_MOTOR_ENABLE2 = 2      #D4
        # self.PIN_MOTOR_EN1 = [12, 15]
        # self.PIN_MOTOR_EN2 = [13, 2]
        self.PIN_CHARGING_DISABLE = 0   #D3
        self.PIN_WAKEUP = 16            #D0
        self.PIN_SCL = 5                #D1
        self.PIN_SDA = 4                #D2
        self.I2C_FREQ = 100000
        # self.INA219_ADDR = 0x40 #64 on the adafruit module
        self.INA219_ADDR = 0x45 #69
        
        self.ppin_led = None
        self.ppin_power_enable = machine.Pin(self.PIN_POWER_ENABLE, machine.Pin.OUT, value=0)
        self.ppin_charging_disable = machine.Pin(self.PIN_CHARGING_DISABLE, machine.Pin.OUT, value=1)
        self.i2c = None
        self.i2c_devices = None
        self.current_sensor = None

        self.BATTERY_SAMPLE_INTERVAL = 1200
        # the following are shade direction constants that make it easier to understand motor direction
        self.LOWER = 0
        self.RAISE = 1
        self.MOTOR_START_SPEED = 30     # speed is in percentage
        self.MOTOR_SPEED_RAMP = 5       # increments to speed up the motor, in percentage
        self.MOTOR_SENSOR_SAMPLE_INTERVAL = 10 # 10 milliSeconds between current samples when the motor is on
        self.MOTOR_AVERAGING_SAMPLES = 4    # the current is sampled this many times because it may be noisy due to the motor

        import array
        # pre-allocate current sample arrays - used by the position routine to threshold motor currents
        self.STARTING_CURRENT_SAMPLE_COUNT = 16
        self.starting_currents = array.array('i', (0 for _ in range(self.STARTING_CURRENT_SAMPLE_COUNT)))
        self.starting_current_next = 0
        self.STOPPING_CURRENT_SAMPLE_COUNT = 16
        self.stopping_currents = array.array('i', (0 for _ in range(self.STOPPING_CURRENT_SAMPLE_COUNT)))
        self.stopping_current_next = 0
        #the following are used by _activate_motor to detect current threshold crossing
        self.averaging_sum = 0
        self.average_current_threshold = 0

    def connect(self):
        """ activates the wlan and polls to see if connected
            returns a tuple:
              - a boolean to indicate successful connection or not
              - a msg to display if connection failed
        """
        import esp, network
        from sys import platform
        from utime import sleep_ms

        if "sleep_type" in dir(esp):
            esp.sleep_type(esp.SLEEP_NONE)  # don't shut off wifi when sleeping
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if platform == 'esp32':
            cfg_info = self._get_cfg_info("wifi_info.txt")
            if not cfg_info:
                setwifi()
            cfg_info = self._get_cfg_info("wifi_info.txt")
            if not cfg_info:
                return False, "Error: could not obtain wifi configuration"
            wlan.connect(cfg_info['SSID'], cfg_info['password'])

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

    @property
    def id(self):
        from machine import unique_id
        id_reversed = unique_id()
        id_binary = [id_reversed[n] for n in range(len(id_reversed) - 1, -1, -1)]
        return "ESP-" + "".join("{:02x}".format(x) for x in id_binary)

    def time(self):
        from ntptime import time as get_ntp_time
        for _ in range(6):
            try:
                self.timestamp = get_ntp_time()
                break
            except Exception as e:
                print("Exception in get NTP: {}".format(str(e)))
        return self.timestamp

    def sleep(self,msg=None):
        import machine
        from sys import exit
        from utime import sleep_ms
        try:
            import webrepl
        except:
            webrepl = None

        reset_timeout = 120000 # 3 minutes in milliseconds

        LOG_FILENAME = "./log.txt"
        if msg is not None:
            print(msg)
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write(msg + "\n")

        if self._current_state['params']['sleep'] < 1:
            # exit: stop the infinite loop of main & deep-sleep
            print("Staying awake due to sleep parameter < 1.")
            if webrepl is not None:
                webrepl.start()
                # configure timer to reset after a period, so the device will fetch a new shadow state
                tim = machine.Timer(-1)
                tim.init(period=reset_timeout, mode=machine.Timer.ONE_SHOT, callback=lambda t:machine.reset())
            exit(0)

        print("Going to sleep for {0} seconds.".format(self._current_state['params']['sleep']))
        if "RTC" in dir(machine):
            if webrepl is not None: webrepl.stop()
            sleep_ms(1000)
            rtc = machine.RTC()
            rtc.irq(trigger=rtc.ALARM0, wake=machine.DEEPSLEEP)
            # multiply sleep time by approx 1000 (left shift by 10)
            rtc.alarm(rtc.ALARM0, self._current_state['params']['sleep'] << 10)
            machine.deepsleep()
        else:
            print("No deep sleep yet.. emulating")
            sleep_ms(self._current_state['params']['sleep'] << 10)
            machine.reset()

    # @property
    def _reported_state_get(self):
        # add current arrays as strings to report
        if self.starting_currents[0] != 0:
            currents = ""
            for i in self.starting_currents:
                currents = currents + str(i)+ " "
            # remove trailing space
            self._reported_state['starting_currents'] = currents[:-1]

            # stopping currents start at next and loop around
            currents = ""
            idx = self.stopping_current_next
            for _ in range(self.STOPPING_CURRENT_SAMPLE_COUNT):
                currents = currents + str(self.stopping_currents[idx]) + " "
                idx = idx + 1
                if idx > (self.STOPPING_CURRENT_SAMPLE_COUNT - 1):
                    idx = 0
            self._reported_state['stopping_currents'] = currents[:-1]

        """Add battery voltage, current to report dictionary to be sent to AWS-IOT.
            compares the shadow metadata timestamp with the NTP time to determine if an update should occur
            The shadow timestamp is from 1970-01-01 whereas micropython is 2000-01-01, so subtract 946684800 in the comparison
            """
        do_update = False
        if 'metadata' in self._shadow_state and \
                        'reported' in self._shadow_state['metadata'] and \
                        'batteryVoltage' in self._shadow_state['metadata']['reported']:
            bv_ts = self._shadow_state['metadata']['reported']['batteryVoltage']['timestamp']
            delta_ts = self.timestamp + 946684800 - bv_ts
            # print("metadata_ts: {0}  -- ntp_ts: {1} -- delta: {2}".format(bv_ts, timestamp, delta_ts))
            if delta_ts > self.BATTERY_SAMPLE_INTERVAL:
                do_update = True
        else:
            do_update = True

        if do_update and self.current_sensor is not None:
            self.current_sensor.start()
            self._reported_state['batteryVoltage'] = self.current_sensor.get_bus_mv()
            self._reported_state['batteryCurrent'] = self.current_sensor.get_current_ma()
            self.current_sensor.stop()

        return super()._reported_state_get()

    reported_state = property(_reported_state_get)

    # @property
    def _shadow_state_get(self):
        return super()._shadow_state_get()

    # @shadow_state.setter
    def _shadow_state_set(self, shadow_state):
        if self._has_history and self._restored_state['history'][0]['done'] != 1:
            # The operation didn't finished, so update the status to be reflected in an updated report
            failed_operation = self._restored_state['history'][0]['op']
            self._current_state['params'][failed_operation] = "unknown"
        super()._shadow_state_set(shadow_state)

    shadow_state = property(_shadow_state_get, _shadow_state_set)


    def blink_led(self, t=50):
        import machine
        from utime import sleep_ms
        if self.ppin_led is None:
            self.ppin_led = machine.Pin(self.PIN_LED, machine.Pin.OUT, value=0)
        else:
            self.ppin_led.init(machine.Pin.OUT)
        sleep_ms(t)
        # leave the pin as an input to not mess up any pin state in reset requirements
        self.ppin_led.init(machine.Pin.IN)

    def _persist_state(self):
        """ the ESP can use RTC memory for a persistent store instead of the flash filesystem"""
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

    def _position(self):
        """ validates shadow state variables: position, duration, reverse -- and then calls _activate_motor
            Inputs: shadow_state
            Returns: status string
        """
        status = self._instance_current_sensor()
        if status is not None:
            return "Fail (position): "  + status

        MAX_DURATION = 59
        POSITIONS = ['open', 'closed', 'half']
        class Positions:
            OPEN = 0
            CLOSED = 1
            HALF = 2

        if self._shadow_state['state']['desired']['position'] not in POSITIONS:
            return "Error: unrecognized position: {0}".format(self._shadow_state['state']['desired']['position'])
        if self._shadow_state['state']['desired']['duration'] > MAX_DURATION:
            return "Error: duration: {0} is longer than max value: {1}".format(self._shadow_state['state']['desired']['duration'], MAX_DURATION)
        if self._shadow_state['state']['desired']['duration'] < 1:
            return "Error: duration: {0} is zero or negative".format(self._shadow_state['state']['desired']['duration'])
    
        # if POST fails, then AWS-IOT state could mismatch persisted/real state
        if self._current_state['params']['position'] == self._shadow_state['state']['desired']['position']:
            return "Already in position: " + self._shadow_state['state']['desired']['position']
        
        duration = self._shadow_state['state']['desired']['duration'] * 1000 #convert to millisecond
        
        direction = self.LOWER
        if self._shadow_state['state']['desired']['position'] == POSITIONS[Positions.OPEN]:
            direction = self.RAISE
        # halve the duration if currently in the half position or going to the half position
        if self._shadow_state['state']['desired']['position'] == POSITIONS[Positions.HALF]:
            duration = duration >> 1
            if self._current_state['params']['position'] == POSITIONS[Positions.CLOSED]:
                direction = self.RAISE
        # if the current position is unknown, the direction will be to lower, and the current position will stay unknown.
        if self._current_state['params']['position'] == POSITIONS[Positions.HALF]:
            duration = duration >> 1;
        
        # invert direction if shade is of type 'top_down', as indicated by bit 1: 0x0010
        if (self._shadow_state['state']['desired']['reverse'] & 2) != 0:
            direction = direction^1
        
        # dont move much when lowering if position is unknown since the current sensor can't be used to figure the stopping position
        if self._current_state['params']['position'] not in POSITIONS and direction == self.LOWER:
            duration = 200;

        # it takes longer to go the same distance when raising, so increase the duration by about 10%
        if direction == self.RAISE:
            duration = duration + (duration >> 4) + (duration >> 5)
        
        measured_duration = self._activate_motor(duration, direction)
        if measured_duration > 1:
            if direction == self.RAISE or measured_duration >= duration:
                self._current_state['params']['position'] = self._shadow_state['state']['desired']['position']
            else:
                self._current_state['params']['position'] = 'unknown'

            status = "Done: new position: {0}; motor on for: {1} msec".format(self._current_state['params']['position'], measured_duration)
            if self.averaging_sum > self.average_current_threshold:
                status = status + "; Current: {0} over Threshold: {1}".format(self.averaging_sum, self.average_current_threshold)
        else:
            status = "Error on position; I2C error: {0}".format(measured_duration)
        return status

    def _set_position(self):
        """ Used to set the persisted 'position' to the current desired position.
            Normally used to transition out of the position==unknown state
            """
        self._current_state['params']['position'] = self._shadow_state['state']['desired']['position']
        return "position set to: " + self._current_state['params']['position']

    def _test_current_sensor(self):
        status = self._instance_current_sensor()
        if self.current_sensor is None:
            status = "Fail (current test): " + status
        else:
            self.current_sensor.start()
            mAmps = self.current_sensor.get_current_ma()
            mVolts = self.current_sensor.get_bus_mv()
            status = "Pass: Current: {0:d}  BusVolts: {1:d}".format(mAmps,mVolts)
            self.current_sensor.stop()
        return status

    def _test_motor(self):
        """ Operates the motor for the number of milliseconds specified in test_param
            A negative test_param operates the motor in the reverse direction
            """
        status = self._instance_current_sensor()
        if status is not None:
            status = "Fail (motor test): " + status
        else:
            duration = self._shadow_state['state']['desired']['test_param']
            if duration < 0:
                direction = self.LOWER
            else:
                direction = self.RAISE
            duration = abs(duration)
            if duration < 55000:
                measured_duration = self._activate_motor(duration, direction)
                if measured_duration >= duration:
                    status = "Pass: motor on for " + str(measured_duration) + " msec."
                else:
                    status = "Fail: motor on for " + str(measured_duration) + " msec."
            else: status = "Fail: test_param too large for motor test: " + str(duration)
        return status

    def _instance_current_sensor(self):
        import machine
        from ina219 import INA219
        status = None
        if  self.current_sensor is None:
            if self.i2c is None:
                self.i2c = machine.I2C(scl=machine.Pin(self.PIN_SCL), sda=machine.Pin(self.PIN_SDA), freq=self.I2C_FREQ)
            # the following is commented out to save power
            # try:
            #     self.i2c_devices = self.i2c.scan()
            # except:
            #     return "Exception i2c bus scan"
            # if (len(self.i2c_devices) < 1):
            #     return "No i2c devices detected"
            try:
                self.current_sensor = INA219(i2c=self.i2c, i2c_addr=self.INA219_ADDR)
                if not self.current_sensor.in_standby_when_initialized:
                    print("Current sensor not in standby.")
                    self.current_sensor.stop()
            except:
                status = "I2C access to current sensor failed"
        return status

    def _activate_motor(self, duration, direction):
        """Instantiates a motor and turns on the motor driver for the duration (expressed in milliseconds)
            """
        from motor import Motor
        import machine, utime
        
        elapsed_time = 0
        self.average_current_threshold = self._shadow_state['state']['desired']['threshold'] * self.MOTOR_AVERAGING_SAMPLES
        
        #invert direction if reverse bit is set
        motor_direction = direction ^ (self._shadow_state['state']['desired']['reverse'] & 1)
        self.motor = Motor(self.PIN_MOTOR_ENABLE1, self.PIN_MOTOR_ENABLE2)

        #enable 12V and disable charging
        if self.ppin_power_enable is None:
            self.ppin_power_enable = machine.Pin(self.PIN_POWER_ENABLE, machine.Pin.OUT, value=1)
        else:
            self.ppin_power_enable.value(1)
        
        if self.ppin_charging_disable is None:
            self.ppin_charging_disable = machine.Pin(self.PIN_CHARGING_DISABLE, machine.Pin.OUT, value=0)
        else:
            self.ppin_charging_disable.value(0)
        utime.sleep_ms(50) # wait for power to stabilize

        self.current_sensor.start()
        start_ticks = utime.ticks_ms()
        self.motor.start(direction=motor_direction, speed=self.MOTOR_START_SPEED)
        
        while elapsed_time < duration:
            sample_ticks_begin = utime.ticks_ms()
            current_sample = self._update_current_arrays()
            
            # !! TODO: stop calling adjustSpeed after calling it N times
            if (current_sample < self._shadow_state['state']['desired']['threshold']):
                self.motor.adjust_speed(self.MOTOR_SPEED_RAMP)
            
            # check the avg of the last N samples don't exceed the threshold
            self.averaging_sum = 0
            for averaging_offset in range(-self.MOTOR_AVERAGING_SAMPLES, 0):
                averaging_index = self.stopping_current_next + averaging_offset
                if (averaging_index < 0):
                    averaging_index += self.STOPPING_CURRENT_SAMPLE_COUNT
                self.averaging_sum += self.stopping_currents[averaging_index]
#                print("Index: {0} -- Value: {1} -- Sum: {2}".format(averaging_index, self.stopping_currents[averaging_index], self.averaging_sum))
            
            # stop the motor if over the threshold
            if self.averaging_sum > self.average_current_threshold:
                break;
            
            delay_time = self.MOTOR_SENSOR_SAMPLE_INTERVAL - (utime.ticks_ms() - sample_ticks_begin)
            utime.sleep_ms(delay_time)
            elapsed_time = utime.ticks_ms() - start_ticks;

        self.motor.stop()
        self.ppin_power_enable.value(0)
        self.ppin_charging_disable.value(1)
        # re-init pins so they are tri-state
        self.ppin_power_enable.init(machine.Pin.IN)
        self.ppin_charging_disable.init(machine.Pin.IN)
        self.motor.deinit()
        del self.motor
        self.current_sensor.stop()
        return elapsed_time

    def _update_current_arrays(self):
        import utime
        #assumes caller has already instanced & checked & started the sensor
        
        current_sample = self.current_sensor.get_current_ma()
        for samples in range(3):
            utime.sleep_ms(5)
            current_sample += self.current_sensor.get_current_ma()
        current_sample = current_sample >> 2 #take average of 4 samples
        
        if (self.starting_current_next < self.STARTING_CURRENT_SAMPLE_COUNT):
            self.starting_currents[self.starting_current_next] = current_sample;
            self.starting_current_next += 1;
        
        # continuously update stopping array; when at end, loop back to the beginning
        self.stopping_currents[self.stopping_current_next] = current_sample
        self.stopping_current_next += 1
        if (self.stopping_current_next > self.STOPPING_CURRENT_SAMPLE_COUNT-1):
            self.stopping_current_next = 0
        return current_sample

    def _get_cfg_info(filename):
        # TODO: get the info from a secure store instead of the flash filesystem
        import ujson
        try:
            with open(filename) as f:
                cfg_info = ujson.load(f)
            return cfg_info
        except OSError as e:
            e_str = str(e)
            print("Exception (get_cfg_info) filename: {}   Error: {}".format(filename, e_str))
            return None

