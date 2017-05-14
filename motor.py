class Motor(object):
    """A motor controller
        
        Attributes:
        enable_pins: PWM pins on the device, one or the other will be driven by a duty cycle based on the direction
        speed_setting: The current speed; range 0 to 100.  Used to calculate the PWM duty cycle
        direction: boolean for forward or not
        """
    
    def __init__(self, enable_pin1, enable_pin2):
        """Enables the PWM pins"""
        import machine
        self.forward = False
        self.enable1 = enable_pin1
        self.enable2 = enable_pin2
        self.speed_setting = 0
        _pwm_freq = 1000 #DRV8871 has a freq range of 0 to 100kHz; ESP8266 range is 1000
        #duty is from 0 to 1023
        self.pin1 = machine.PWM(machine.Pin(self.enable1), freq=_pwm_freq, duty=0)
        self.pin2 = machine.PWM(machine.Pin(self.enable2), freq=_pwm_freq, duty=0)
        #example: pin12 = machine.PWM(machine.Pin(12), freq=1000, duty=256)
        
    def __del__(self):
        # it doesn't appear __del__ gets called when deleting the class, so allowing deinit to be called directly
        self.deinit()

    def start(self, direction=True, speed=40):
        """Start the motor at the specified speed and rotation direction."""
        self.forward = direction
        # expect speed to be in the range of zero to 100.  Multiply by 10
        self.speed_setting = speed
        duty_cycle = speed * 10
        if (duty_cycle > 1000):
            duty_cycle = 1023
        if (self.forward):
            self.pin1.duty(duty_cycle)
        else:
            self.pin2.duty(duty_cycle)

    def stop(self):
        # to stop, both pins should be low
        self.speed_setting = 0
        self.pin1.duty(self.speed_setting)
        self.pin2.duty(self.speed_setting)

    def adjust_speed(self, delta):
        #new_speed = (self.speed_setting * 205) >> 11 # equivalent to divide by 10
        new_speed = self.speed_setting + delta
        self.start(self.forward, new_speed)

    def deinit(self):
        # print("de-init the motor Enable pins.")
        import machine
        self.pin1.deinit()
        self.pin2.deinit()
        #leave the pins floating
        self.pin1.init(machine.Pin.IN)
        self.pin2.init(machine.Pin.IN)
