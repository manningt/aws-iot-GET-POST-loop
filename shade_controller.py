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
    STARTING_CURRENT_SAMPLE_COUNT = const(32)
    starting_currents = array.array('i', (0 for _ in range(STARTING_CURRENT_SAMPLE_COUNT)))
    starting_current_next = 0
    STOPPING_CURRENT_SAMPLE_COUNT = const(16)
    stopping_currents = array.array('i', (0 for _ in range(STOPPING_CURRENT_SAMPLE_COUNT)))
    stopping_current_next = 0
    
    def __init__(self):
        self.PSTATE_FILENAME = "./pstate.txt"
        # the following are the GPIO numbers; the labels refer to the nodeMCU development board
        self.PIN_LED = 2
        self.PIN_MOTOR_ENABLE1 = 12     #label D6 on the nodeMCU board
        self.PIN_MOTOR_ENABLE2 = 14     #D5
        self.PIN_POWER_ENABLE = 13      #D7
        self.PIN_CHARGING_DISABLE = 2   #D4
        self.PIN_WAKEUP = 16            #D0
        
        self.PIN_SCL = 5                #D1
        self.PIN_SDA = 4                #D2
        self.I2C_FREQ = 100000
        #self.INA219_ADDR = 0x40 #64 on the adafruit module
        self.INA219_ADDR = 0x45 #69
        
        self.MOTOR_START_SPEED = 30     # speed is in percentage
        self.MOTOR_SPEED_RAMP = 5       # increments to speed up the motor, in percentage
        self.MOTOR_SENSOR_SAMPLE_INTERVAL = 10 # 10 milliSeconds between current samples when the motor is on
        self.MOTOR_AVERAGING_SAMPLES = 4    # the current is sampled this many times because it may be noisy due to the motor
        # the following are shade direction constants that make it easier to understand motor direction
        self.LOWER = 0
        self.RAISE = 1

        self.BATTERY_SAMPLE_INTERVAL = 15
        self.BATTERY_LOG_PREFIX = "battery_log_"
        
        """ the following dictionary are functions that are called when a desired state variable changes:
            the dictionary would be modified to expose more variables/functions for different devices
            """
        self.DISPATCH = {'test': self._dispatch_test, 'test_param': self._dispatch_test, 'position' : self._position}

        """pstate are parameters that are stored in flash and over-written by desired values provided by the AWS-IOT Shadow
            - position is persisted because it may not be equal to the desired value (uninitialized or in error)
            - sleep & awake are persisted because they are used even if unable to get the desired values from AWS
            - test and test_param are persisted so they can be compared to the new desired state
            
            the pstate can get updated to reflect the AWS shadow desired state.
            However, the persisted position is the REAL position, which may not be the desired position
            
            pstate starts with defaults, which will get over-written by the values read from persistent store (flash)
            """
        self.pstate = {'sleep':200, 'awake':4, 'test':'none', 'test_param':0, 'position':'unknown'}
        self.restored_state = None
        #NON-PERSISTED_STATE = ('duration', 'reverse', 'threshold')
        
        self.ppin_led = None
        self.ppin_power_enable = None
        self.ppin_charging_disable = None
        self.motor1 = None
        self.i2c = None
        self.i2c_devices = None
        self.current_sensor = None
    
        self._restore_state()
    
        import machine
        id_reversed = machine.unique_id()
        id_binary = [id_reversed[n] for n in range(len(id_reversed) - 1, -1, -1)]
        self.id = 'ESP-' + ''.join('{:02x}'.format(x) for x in id_binary)


    def process_shadow_state(self):
        """ Input: is an updated shadow_state at the class level; shadow_state was not passed in as an arg to avoid the memory allocation
            Return: None
            Output: an updated reported_state at the class level, which does not have to be used immediately
        """
        status = self._instance_current_sensor()
        if (self.current_sensor is not None) and not self.current_sensor.in_standby_when_initialized:
            print("Current sensor not in standby on restart")
            #! TODO: turn off 12V
            self.current_sensor.stop()

        # the following state variables do not have an 'action'; just update them so they'll be persisted after the 'action' is done
        self.pstate['sleep'] = self.shadow_state['state']['desired']['sleep']
        self.pstate['awake'] = self.shadow_state['state']['desired']['awake']
        # test and position variables are updated in the function that processes them.
            
        for key in self.DISPATCH:
            #thing.dispatch is a dictionary of functions whose name can match an IOT state variable
            if key in self.shadow_state['state']['delta']:
                if not (key == 'position' and self.pstate['position'] == 'unknown'):
                    status = self.DISPATCH[key]()
                    # dispatch only one delta per GET/POST cycle so the status is updated serially
                    break
        
        # persist the changes of state due to the dispatch, if any
        if self._persist_state() is not None:
            print("Error: persisting pstate failed.")
        
        if status is not None:
            self.reported_state['status'] = status
        
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



    def blink_led(self, t=50):
        import machine, utime
        if self.ppin_led is None:
            self.ppin_led = machine.Pin(self.PIN_LED, machine.Pin.OUT, value=0)
        else:
            self.ppin_led.init(mode=machine.Pin.OUT)
        utime.sleep_ms(t)
        # leave the pin as an input to not mess up any pin state in reset requirements
        self.ppin_led.init(mode=machine.Pin.IN)
        return


    def goto_sleep(self,cause=None):
        import webrepl, machine, sys, utime
        LOG_FILENAME = "./log.txt"
        
        if cause is not None:
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write(cause + "\n")

        if 'sleep' in self.pstate:
            if self.pstate['sleep'] < 1:
                # exit: stop the infinite loop of main & deep-sleep
                print("Exit due to sleep parameter < 1.")
                with open(LOG_FILENAME, 'a') as log_file:
                    log_file.write("Exit due to sleep parameter < 1.\n")
                sys.exit(0)
        else:
            with open(LOG_FILENAME, 'a') as log_file:
                log_file.write("Exit due missing sleep parameter.\n")
            sys.exit(-1)
        
        start_ticks = utime.ticks_ms()
        elapsed_msecs = 0
        # multiply awake time by 1024 to convert from seconds to milliseconds
        while elapsed_msecs < (self.pstate['awake'] << 10):
            for _ in range(3):
                self.blink_led()
                utime.sleep_ms(100)
            utime.sleep_ms(1900)
            elapsed_msecs = utime.ticks_diff(utime.ticks_ms(), start_ticks)

        print("Going to sleep for {0} seconds.".format(self.pstate['sleep']))
        webrepl.stop()
        utime.sleep_ms(1000)
        rtc = machine.RTC()
        rtc.irq(trigger=rtc.ALARM0, wake=machine.DEEPSLEEP)
        # multiply sleep time by approx 1000 (left shift by 10)
        rtc.alarm(rtc.ALARM0, self.pstate['sleep'] << 10)
        machine.deepsleep()


    def check_conditions(self, date, time):
        """Creates battery voltage, current and charge rate dictionary to send to AWS-IOT.
            Uses a file to persist last voltage measurement.
            File name convention is: battery_log_YYYYMMDD.txt
            log string convention is: HHMMSS: mVolts

            Input: date (YYYYMMDD) & time (HHMMSS)
            Return: None
            Output: an updated reported_state at the class level and battery_log files
            """

        write_log = False
        previous_mvolts = None
        delta_hours = 0
        delta_minutes = 0
        fname = self.BATTERY_LOG_PREFIX + date + ".txt"
        s = None
        
        try:
            with open(fname) as f:
                s = f.read()
        except OSError:
            write_log = True
        
        if s is not None and len(s) > 0:
            # test if the last sample time was greater than N minutes ago
            lines = s.split()
            if len(lines) > 0 and len(lines[-1]) > 10:
                last_log_hour = int(lines[-1][0:2])
                now_hour = int(time[0:2])
                delta_hours = now_hour - last_log_hour
#                print("  hours: last: {0}  now:  {1}  delta {2}".format(last_log_hour, now_hour, delta_hours))
                # there is no hours roll-over detect, because each day starts a new log
                
                last_log_minutes = int(lines[-1][2:4])
                now_minutes = int(time[2:4])
                delta_minutes = now_minutes - last_log_minutes
#                print("minutes: last: {0}  now:  {1}  delta {2}".format(last_log_minutes, now_minutes, delta_minutes))
                if delta_minutes < 0:
                    delta_minutes += 60
                    delta_hours -= 1
                delta_minutes += delta_hours * 60
#                print("final delta minutes: {0}".format(delta_minutes))

                if delta_minutes > self.BATTERY_SAMPLE_INTERVAL:
                    write_log = True
                    previous_mvolts = int(lines[-1].split(":")[1])
            else: print("Unexpected format in battery_log: {0}".format(lines[-1]))
        else: write_log = True

        if write_log and self.current_sensor is not None:
            self.current_sensor.start()
            self.reported_state['batteryVoltage'] = self.current_sensor.get_bus_mv()
            self.reported_state['batteryCurrent'] = self.current_sensor.get_current_ma()
            self.current_sensor.stop()
            if previous_mvolts is not None:
                self.reported_state['batteryChargingRate'] =  self.reported_state['batteryVoltage'] - previous_mvolts
            
            with open(fname, "a") as f:
                f.write(time + ":" + str(self.reported_state['batteryVoltage']) + "\n")
            self._cull_battery_logs()


    def _cull_battery_logs(self):
        #remove old log files, but keep the last 5
        import uos
        file_list = uos.listdir()
        log_file_list = []
        for filename in file_list:
            if self.BATTERY_LOG_PREFIX in filename:
                log_file_list.append(filename)
        log_file_list.sort()
        if len(log_file_list) > 8:
            while len(log_file_list) > 5:
                uos.remove(log_file_list[0])
                del log_file_list[0]


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
#        print("restored_state: ", end="")
#        print(self.restored_state)
        if state_change:
#            print("Persisting pstate: ", end="")
#            print(self.pstate)
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

        LEGAL_POSITIONS = ('open', 'close', 'half')
        MAX_DURATION = 59

        if self.shadow_state['state']['desired']['position'] not in LEGAL_POSITIONS:
            return "Error: unrecognized position: {0}".format(self.shadow_state['state']['desired']['position'])
        if self.shadow_state['state']['desired']['duration'] > MAX_DURATION:
            return "Error: duration: {0} is longer than max value: {1}".format(self.shadow_state['state']['desired']['duration'], MAX_DURATION)
        if self.shadow_state['state']['desired']['duration'] < 1:
            return "Error: duration: {0} is zero or negative".format(self.shadow_state['state']['desired']['duration'])
    
        # if POST fails, then AWS-IOT state could mismatch persisted/real state
        if self.pstate['position'] == self.shadow_state['state']['desired']['position']:
            return "Already in position: " + self.shadow_state['state']['desired']['position']
        
        duration = self.shadow_state['state']['desired']['duration'] * 1000; #convert to millisecond
        
        direction = self.LOWER
        if self.shadow_state['state']['desired']['position'] == 'open': direction = self.RAISE
        if self.shadow_state['state']['desired']['position'] == 'half':
            if self.pstate['position'] == 'closed': direction = self.RAISE
            # if the current position is unknown, the direction will be to lower, and the current position will stay unknown.
            duration = duration >> 1;
        #halve the duration if currently in the half position (desired should be open or closed)
        if self.pstate['position'] == 'half': duration = duration >> 1;
        
        # invert direction if top_down, as indicated by bit 1: 0x0010
        if (self.shadow_state['state']['desired']['reverse'] & 2) != 0:
            direction = ~direction
        
        # dont move much when lowering if position is unknown since the current sensor can't be used to figure the stopping position
        if self.pstate['position'] == 'unknown' and direction == self.LOWER:
            duration = 200;
        
        measured_duration = self._activate_motor(duration, direction);
        if measured_duration > 1:
            if direction == self.RAISE or measured_duration >= duration:
                self.pstate['position'] = self.shadow_state['state']['desired']['position']
            else:
                self.pstate['position'] = 'unknown'

            status = "Done: new position: {0}; motor on for: {1} msec".format(self.pstate['position'], measured_duration)
    #        !! add threshold crossing text to status
    #        if (currentThresholdCrossed()) sprintf(status_string, "%s; %s", status_string, THRESHOLD_CROSSED);
        else:
            status = "Error on position; I2C error: {0}".format(measured_duration)
        
        return status


    ''' ==== tests that can be run when the 'test' parameter changes to a new value  ===='''
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
            status = "Unrecognized test: " + testname
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
            try:
                self.i2c_devices = self.i2c.scan()
            except:
                return "Exception i2c bus scan"
            if (len(self.i2c_devices) < 1):
                return "No i2c devices detected"
            self.current_sensor = INA219(i2c=self.i2c, i2c_addr=self.INA219_ADDR)
        return None
    

    def _activate_motor(self, duration, direction):
        """Instantiates a motor and turns on the motor driver for the duration (expressed in milliseconds)
            """
        from motor import Motor
        import machine, utime
        
        # clear current history data
        self.starting_current_next = 0
        self.starting_currents=[0 for _ in range(len(self.starting_currents))]
        self.stopping_current_next = 0
        self.stopping_currents=[0 for _ in range(len(self.stopping_currents))]

        elapsed_time = 0;
        #threshold_crossed_count = 0;
        #current_threshold_crossed = False
        average_current_threshold = self.shadow_state['state']['desired']['threshold'] * self.MOTOR_AVERAGING_SAMPLES
        
        #invert direction if reverse bit is set
        motor_direction = direction ^ (self.shadow_state['state']['desired']['reverse'] & 1)
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

        if self.motor1 is None:
            self.motor1 = Motor(self.PIN_MOTOR_ENABLE1, self.PIN_MOTOR_ENABLE2)
        self.current_sensor.start()

        start_ticks = utime.ticks_ms()
        self.motor1.start(direction=motor_direction, speed=self.MOTOR_START_SPEED)
        
        while elapsed_time < duration:
            sample_ticks_begin = utime.ticks_ms()
            current_sample = self._update_current_arrays()
            
            # !! TODO: stop calling adjustSpeed after calling it N times
            if (current_sample < self.shadow_state['state']['desired']['threshold']):
                self.motor1.adjust_speed(self.MOTOR_SPEED_RAMP)
            
            # check the avg of the last N samples don't exceed the threshold
            averaging_sum = 0
            for averaging_offset in range(-self.MOTOR_AVERAGING_SAMPLES, 0):
                averaging_index = self.stopping_current_next + averaging_offset
                if (averaging_index < 0):
                    averaging_index += self.STOPPING_CURRENT_SAMPLE_COUNT
                averaging_sum += self.stopping_currents[averaging_index]
            
            # stop the motor if over the threshold
            if averaging_sum > average_current_threshold:
                break;
            
            delay_time = self.MOTOR_SENSOR_SAMPLE_INTERVAL - (utime.ticks_ms() - sample_ticks_begin)
            utime.sleep_ms(delay_time)
            elapsed_time = utime.ticks_ms() - start_ticks;

        self.motor1.stop()
        self.ppin_power_enable.value(0)
        self.ppin_charging_disable.value(1)
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
