class Shade_controller:
    """ This class holds state info for a device.  The state info controls the devices behavior.
        The expected procedure to be performed by the instantiator of this class are:
         * instantiate the class
         * update the shadow state
         * process_shadow_state(): this invokes any actions the device should perform on a state change
                                    and report state changes in the report_state dictionary
         * check_conditions(): this adds coditions (temperature, battery voltage, etc) to the reported_state
        
        """
    # the following state can be directly accessed by the instantiating module
    shadow_state = {}       #holds the shadow state obtained from AWS-IOT; not modified by the controller
    reported_state = {}     #holds the state to be posted to the shadow; written by the controller
    id = None               #An unique ID for the device that can be used as the shadow ID
    
    import array
    # pre-allocate current sample arrays - used by the position routine to threshold motor currents
    STARTING_CURRENT_SAMPLE_COUNT = const(16)
    starting_currents = array.array('i', (0 for _ in range(STARTING_CURRENT_SAMPLE_COUNT)))
    starting_current_next = 0
    STOPPING_CURRENT_SAMPLE_COUNT = const(16)
    stopping_currents = array.array('i', (0 for _ in range(STOPPING_CURRENT_SAMPLE_COUNT)))
    stopping_current_next = 0
    
    def __init__(self):
        import machine

        self.PSTATE_FILENAME = "./pstate.txt"
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
        
        self.MOTOR_START_SPEED = 30     # speed is in percentage
        self.MOTOR_SPEED_RAMP = 5       # increments to speed up the motor, in percentage
        self.MOTOR_SENSOR_SAMPLE_INTERVAL = 10 # 10 milliSeconds between current samples when the motor is on
        self.MOTOR_AVERAGING_SAMPLES = 4    # the current is sampled this many times because it may be noisy due to the motor
        # the following are shade direction constants that make it easier to understand motor direction
        self.LOWER = 0
        self.RAISE = 1

        self.BATTERY_SAMPLE_INTERVAL = 450 #7 minutes  !!change to 1200
        
        """ the following dictionary are functions that are called when a desired state variable changes:
            the dictionary would be modified to expose more variables/functions for different devices
            """
        self.DISPATCH = {'test': self._dispatch_test, 'test_param': self._dispatch_test, 'position' : self._position}

        """pstate are parameters that are stored in flash and over-written by desired values provided by the AWS-IOT Shadow
            - position is persisted because it may not be equal to the desired value (uninitialized or in error)
            - sleep is persisted because they are used even if unable to get the desired values from AWS
            - test and test_param are persisted so they can be compared to the new desired state
            
            the pstate can get updated to reflect the AWS shadow desired state.
            However, the persisted position is the REAL position, which may not be the desired position
            
            pstate starts with defaults, which will get over-written by the values read from persistent store (flash)
            
            the default of 0 for sleep allows recovery if the network is not reachable.
            """
        self.pstate = {'sleep':0, 'test':'unknown', 'test_param':0, 'position':'unknown'}
        self.restored_state = None
        #NON-PERSISTED_STATE = ('duration', 'reverse', 'threshold')

        self.ppin_led = None
        self.ppin_power_enable = machine.Pin(self.PIN_POWER_ENABLE, machine.Pin.OUT, value=0)
        self.ppin_charging_disable = machine.Pin(self.PIN_CHARGING_DISABLE, machine.Pin.OUT, value=1)
        self.i2c = None
        self.i2c_devices = None
        self.current_sensor = None
        #the following are used by _activate_motor to detect current threshold crossing
        self.averaging_sum = 0
        self.average_current_threshold = 0
    
        self._restore_state()
    
        id_reversed = machine.unique_id()
        id_binary = [id_reversed[n] for n in range(len(id_reversed) - 1, -1, -1)]
        self.id = 'ESP-' + ''.join('{:02x}'.format(x) for x in id_binary)
        # command history is stored in the RTC
        self.previous_action = None
        self.history = None
        if "RTC" in dir(machine):
            self.rtc = machine.RTC()
            self.history = self.rtc.memory().decode("utf8").split("\n")
            if len(self.history[0]) > 0:
                print("Last action: " + self.history[0])
                self.previous_action = self.history[0].split("|")
                #previous action = strt/done|key|value|metadata_timestamp|status
                if len(self.previous_action) < 4:
                    self.previous_action = None

    def process_shadow_state(self):
        """ Input: is an updated shadow_state at the class level; shadow_state was not passed in as an arg to avoid the memory allocation
            Return: None
            Output: an updated reported_state at the class level, which does not have to be used immediately
        """
        import machine
        status = self._instance_current_sensor()
        current_action = None
        if self.current_sensor is not None and not self.current_sensor.in_standby_when_initialized:
            print("Current sensor not in standby.")
            self.current_sensor.stop()
        if self.previous_action is not None and self.previous_action[0] != "done":
            #The means the operating (test or reposition) was interrupted before it finished
            # so do the following to clean-up and to avoid re-running the same command
            self.pstate['test'] = "unknown"
            self.pstate['position'] = "unknown"
            status = "Error: state change: {}: {} failed before completion".format(self.previous_action[1], self.previous_action[2])

        # the following state variables do not have an 'action'; just update them so they'll be persisted after the 'action' is done
        self.pstate['sleep'] = self.shadow_state['state']['desired']['sleep']
        # test and position variables are updated in the function that processes them.

        if self.pstate['test'] == 'unknown':
            # initial condition - this avoid re-doing the test if the pstate was not restored
            self.pstate['test'] = self.shadow_state['state']['desired']['test']
            self.pstate['test_param'] = self.shadow_state['state']['desired']['test_param']

        for key in self.DISPATCH:
            # the compare with unknown prevents a position command from being dispatched
            shadow_state_changed = self.shadow_state['state']['desired'][key] != self.pstate[key] and self.pstate[key] != "unknown"
            if self.previous_action is None:
                timestamp_changed = True
            else:
                timestamp_changed = self.shadow_state['metadata']['desired'][key] != self.previous_action[3]
            if shadow_state_changed and timestamp_changed:
                current_action = "strt|{}|{}|{}|TBD\n".format(key,self.shadow_state['state']['desired'][key], self.shadow_state['metadata']['desired'][key]['timestamp'])
                if "RTC" in dir(machine):
                    if self.history is not None:
                        self.rtc.memory(current_action + self.history[0])
                    else:
                        self.rtc.memory(current_action)
                status = self.DISPATCH[key]()
                # dispatch only one delta per GET/POST cycle so the status is updated serially
                break

        # persist the changes of state due to the dispatch, if any
        if self._persist_state() is not None:
            print("Error: persisting pstate failed.")
        
        for key, value in self.shadow_state['state']['desired'].items():
            # only report changed variables in order to minimize shadow versions
            # On start-up, there is no reported dictionary
            if key in self.pstate:
                # persisted keys
                if ('reported' not in self.shadow_state['state']) or \
                        (key not in self.shadow_state['state']['reported']) or \
                        (self.shadow_state['state']['reported'][key] != self.pstate[key]):
                    self.reported_state[key] = self.pstate[key]
            else:
                # non-persisted keys
                if ('reported' not in self.shadow_state['state']) or \
                        (key not in self.shadow_state['state']['reported']) or \
                        (self.shadow_state['state']['reported'][key] != value):
                    self.reported_state[key] = value

        if status is not None:
            self.reported_state['status'] = status
            if current_action is not None:
                current_action = current_action.replace("strt","done")
                current_action = current_action.replace("TBD",status)
                if "RTC" in dir(machine):
                    if self.history is not None:
                        self.rtc.memory(current_action + self.history[0])
                    else:
                        self.rtc.memory(current_action)

        if self.starting_currents[0] != 0:
            currents = None
            for i in self.starting_currents:
                if i == 0:
                    currents = "{}".format(i)
                else:
                    currents = "{} {}".format(currents, i)
            self.reported_state['starting_currents'] = currents
            #stopping currents start at next and loop around
            idx = self.stopping_current_next
            for _ in self.stopping_currents:
                if idx == self.stopping_current_next:
                    currents = "{}".format(self.stopping_currents[idx])
                else:
                    currents = "{} {}".format(currents, self.stopping_currents[idx])
                idx += 1
                if (idx > self.STOPPING_CURRENT_SAMPLE_COUNT - 1):
                    idx = 0
            self.reported_state['stopping_currents'] = currents

    def blink_led(self, t=50):
        import machine, utime
        if self.ppin_led is None:
            self.ppin_led = machine.Pin(self.PIN_LED, machine.Pin.OUT, value=0)
        else:
            self.ppin_led.init(machine.Pin.OUT)
        utime.sleep_ms(t)
        # leave the pin as an input to not mess up any pin state in reset requirements
        self.ppin_led.init(machine.Pin.IN)
        return

    def goto_sleep(self,cause=None):
        import machine, sys, utime
        try:
            import webrepl
        except:
            webrepl = None

        LOG_FILENAME = "./log.txt"
        
        if cause is not None:
            print(cause)
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write(cause + "\n")

        start_ticks = utime.ticks_ms()
        elapsed_msecs = 0

        if 'sleep' in self.pstate:
            if self.pstate['sleep'] < 1:
                # exit: stop the infinite loop of main & deep-sleep
                print("Staying awake due to sleep parameter < 1.")
                if webrepl is not None:
                    webrepl.start()
                    # configure timer to reset after 3 minutes, then it will fetch a new shadow state
                    tim = machine.Timer(-1)
                    tim.init(period=120000, mode=machine.Timer.ONE_SHOT, callback=lambda t:machine.reset())
                sys.exit(0)
        else:
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write("Exit due missing sleep parameter.\n")
            sys.exit(-1)

        print("Going to sleep for {0} seconds.".format(self.pstate['sleep']))
        if "RTC" in dir(machine):
            webrepl.stop()
            utime.sleep_ms(1000)
            rtc = machine.RTC()
            rtc.irq(trigger=rtc.ALARM0, wake=machine.DEEPSLEEP)
            # multiply sleep time by approx 1000 (left shift by 10)
            rtc.alarm(rtc.ALARM0, self.pstate['sleep'] << 10)
            machine.deepsleep()
        else:
            print("No deep sleep yet.. emulating")
            utime.sleep_ms(self.pstate['sleep'] << 10)
            machine.reset()

    def check_conditions(self, timestamp):
        """Creates battery voltage, current and charge rate dictionary to send to AWS-IOT.
            compares the shadow metadata timestamp with the NTP time to determine if an update should occur
            Note: shadow timestamp is from 1970-01-01 whereas micropython is 2000-01-01
                  So one has to subtract 946684800 from the comparison

            Input: timestano
            Return: None
            Output: an updated reported_state at the class level and battery_log files
            """
        do_update = False
        if 'metadata' in self.shadow_state and 'reported' in self.shadow_state['metadata'] and 'batteryVoltage' in self.shadow_state['metadata']['reported']:
            bv_ts = self.shadow_state['metadata']['reported']['batteryVoltage']['timestamp']
            delta_ts = timestamp + 946684800 - bv_ts
#            print("metadata_ts: {0}  -- ntp_ts: {1} -- delta: {2}".format(bv_ts, timestamp, delta_ts))
            if delta_ts > self.BATTERY_SAMPLE_INTERVAL:
                do_update = True
        else:
            do_update = True

        if do_update and self.current_sensor is not None:
            self.current_sensor.start()
            self.reported_state['batteryVoltage'] = self.current_sensor.get_bus_mv()
            self.reported_state['batteryCurrent'] = self.current_sensor.get_current_ma()
            self.current_sensor.stop()

    def _persist_state(self):
        # only perform flash write if the restored state not equal to current pstate
        state_change = False
        if self.restored_state is not None:
            for key in self.pstate:
                if self.pstate[key] != self.restored_state[key]:
                    state_change = True
                    break
        else:
            state_change = True
        if state_change:
            import ujson
            # there is no ujson.dump, only ujson.dumps in micropython
            try:
                with open(self.PSTATE_FILENAME, "w") as f:
                    f.write(ujson.dumps(self.pstate))
            except OSError:
                return "Error: persisting state failed."
        return None

    def _restore_state(self):
        import ujson
        # if no pstate file then defaults will be used
        try:
            with open(self.PSTATE_FILENAME) as f:
                self.restored_state = ujson.load(f)
            # copy restored state to pstate
            for key in self.restored_state:
                self.pstate[key] = self.restored_state[key]
        except OSError:
            print('Warning (restore_state): no pstate file')

    def _position(self):
        """ validates shadow state variables: position, duration, reverse -- and then calls _activate_motor
            Inputs: shadow_state
            Returns: status string
        """
        if self.current_sensor is None:
            return "Fail (position): no current sensor"

        MAX_DURATION = 59
        POSITIONS = ['open', 'closed', 'half']
        class Positions:
            OPEN = 0
            CLOSED = 1
            HALF = 2

        if self.shadow_state['state']['desired']['position'] not in POSITIONS:
            return "Error: unrecognized position: {0}".format(self.shadow_state['state']['desired']['position'])
        if self.shadow_state['state']['desired']['duration'] > MAX_DURATION:
            return "Error: duration: {0} is longer than max value: {1}".format(self.shadow_state['state']['desired']['duration'], MAX_DURATION)
        if self.shadow_state['state']['desired']['duration'] < 1:
            return "Error: duration: {0} is zero or negative".format(self.shadow_state['state']['desired']['duration'])
    
        # if POST fails, then AWS-IOT state could mismatch persisted/real state
        if self.pstate['position'] == self.shadow_state['state']['desired']['position']:
            return "Already in position: " + self.shadow_state['state']['desired']['position']
        
        duration = self.shadow_state['state']['desired']['duration'] * 1000 #convert to millisecond
        
        direction = self.LOWER
        if self.shadow_state['state']['desired']['position'] == POSITIONS[Positions.OPEN]:
            direction = self.RAISE
        # halve the duration if currently in the half position or going to the half position
        if self.shadow_state['state']['desired']['position'] == POSITIONS[Positions.HALF]:
            duration = duration >> 1
            if self.pstate['position'] == POSITIONS[Positions.CLOSED]:
                direction = self.RAISE
        # if the current position is unknown, the direction will be to lower, and the current position will stay unknown.
        if self.pstate['position'] == POSITIONS[Positions.HALF]:
            duration = duration >> 1;
        
        # invert direction if shade is of type 'top_down', as indicated by bit 1: 0x0010
        if (self.shadow_state['state']['desired']['reverse'] & 2) != 0:
            direction = direction^1
        
        # dont move much when lowering if position is unknown since the current sensor can't be used to figure the stopping position
        if self.pstate['position'] not in POSITIONS and direction == self.LOWER:
            duration = 200;

        # it takes longer to go the same distance when raising, so increase the duration by about 10%
        if direction == self.RAISE:
            duration = duration + (duration >> 4) + (duration >> 5)
        
        measured_duration = self._activate_motor(duration, direction)
        if measured_duration > 1:
            if direction == self.RAISE or measured_duration >= duration:
                self.pstate['position'] = self.shadow_state['state']['desired']['position']
            else:
                self.pstate['position'] = 'unknown'

            status = "Done: new position: {0}; motor on for: {1} msec".format(self.pstate['position'], measured_duration)
            if self.averaging_sum > self.average_current_threshold:
                status = status + "; Current: {0} over Threshold: {1}".format(self.averaging_sum, self.average_current_threshold)
        else:
            status = "Error on position; I2C error: {0}".format(measured_duration)
        return status

    def _dispatch_test(self):
        """ calls one of the test functions based on the string in the shadow_state 'test' variable
            Inputs: shadow_state
            Returns: status string recieved from the test
            """
        DISPATCH = {'none' : self._test_none, 'current': self._test_current_sensor, 'motor': self._test_motor, 'set-position': self._set_position}
        self.pstate['test_param'] = self.shadow_state['state']['desired']['test_param']
        self.pstate['test'] = self.shadow_state['state']['desired']['test']
        if self.pstate['test'] in DISPATCH:
            status = DISPATCH[self.pstate['test']]()
        else:
            status = "Unrecognized test: " + self.pstate['test']
        return status

    def _test_none(self):
        return "test: none"
    
    def _set_position(self):
        """ Used to set the persisted 'position' to the current desired position.
            Normally used to transition out of the position==unknown state
            """
        self.pstate['position'] = self.shadow_state['state']['desired']['position']
        return "position set to: " + self.pstate['position']

    def _test_current_sensor(self):
        if self.current_sensor is None:
            status = "Fail (current test): no current sensor"
        else:
            self.current_sensor.start()
            mAmps = self.current_sensor.get_current_ma()
            mVolts = self.current_sensor.get_bus_mv()
            status = "Pass: Current: {0:d}  BusVolts: {1:d}".format(mAmps,mVolts)
        return status

    def _test_motor(self):
        """ Operates the motor for the number of milliseconds specified in test_param
            A negative test_param operates the motor in the reverse direction
            """
        if self.current_sensor is None:
            status = "Fail (motor test): no current sensor"
        else:
            duration = self.shadow_state['state']['desired']['test_param']
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
            self.current_sensor = INA219(i2c=self.i2c, i2c_addr=self.INA219_ADDR)
        return None

    def _activate_motor(self, duration, direction):
        """Instantiates a motor and turns on the motor driver for the duration (expressed in milliseconds)
            """
        from motor import Motor
        import machine, utime
        
        elapsed_time = 0;
        self.average_current_threshold = self.shadow_state['state']['desired']['threshold'] * self.MOTOR_AVERAGING_SAMPLES
        
        #invert direction if reverse bit is set
        motor_direction = direction ^ (self.shadow_state['state']['desired']['reverse'] & 1)
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
            if (current_sample < self.shadow_state['state']['desired']['threshold']):
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
        return current_sample;
