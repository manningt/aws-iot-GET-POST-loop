# aws-iot-GET-POST-loop
Micropython code which uses the AWS-IOT REST API to GET/POST device state info.

This was only tested on an ESP8266.

There are 4 primary modules:
  * aws_thing_loop: instances a thing, GETs the AWS shadow state for the thing, and POSTs the reported state to AWS
  * base_thing: provides the functions every thing should have: set the shadow_state, get the reported state, get ID, get the current time
  * a thing: inherits from base_thing and adds device specific capabilitie.  There are 3 examples:
        * signal_thing_unix: when the integer value of the 'signal' variable in desired state is changed, the terminal where the script is running will beep
        * signal_thing_esp: similar to signal_thing_unix, an ESP8266 will flash its LED.  On the 8266, it requires pin 16 to be connected to the reset pin for deep sleep to work
        * shade_controller: runs on an ESP; controls a motor to position a shade
  * main.py: calls aws_thing_loop.main()
  
The use case is an IoT application where the device sleeps most of the time (to save battery) and retrieve commands from the cloud when the device wakes up. A motorized window treatment which uses a processor to control a motor and position a shade/curtain is an example. It wakes up and retrieves the desired position every few minutes. If the desired position is different than the current position, the processor controls the motor to change the position to the desired position.

The aws_thing_loop requires this module: https://github.com/manningt/aws-signature-iot-python

The aws_thing_loop requires the following modified micropython modules; the modifications may get absorbed into the standard micropython libraries eventually:
  * urequests: reads the content-length from the header and uses the length when issuing the read for the body.  This is required because AWS does not close the socket after the GET (not compliant with HTTP1.0)
  * hmac_ltd: changed the update to allow binary keys; does not do a copy when asking for the digest; obtain from the aws-signature-iot-python github directory

The shade_controller module uses 2 classes to drive components:
  * motor.py
  * ina219.py: supports putting the INA219 to sleep.  No floating point operations.
  
