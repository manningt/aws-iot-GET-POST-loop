# aws-iot-esp8266-thing
Micropython code which uses the AWS-IOT REST API to GET/POST device state info.

Although the code does not use ESP specific functions, it was only tested on an ESP8266.

There are 3 primary modules:
  * aws_thing_loop: instances a thing, GETs the AWS shadow state for the thing, and POSTs the reported state to AWS
  * the thing: an example instance of this class is a shade_controller, which controls a motor to position a shade
  * main.py: calls aws_thing_loop.main()
  
The use case is an IoT application where the device sleeps most of the time (to save battery) and retrieve commands from the cloud when the device wakes up. A motorized window treatment which uses a processor to control a motor and position a shade/curtain is an example. It wakes up and retrieves the desired position every few minutes. If the desired position is different than the current position, the processor controls the motor to change the position to the desired position.

The aws_thing_loop requires this module: https://github.com/manningt/aws-signature-iot-python

The aws_thing_loop requires the following modified micropython modules; the modifications may get absorbed into the standard micropython libraries eventually:
  * ntptime.py: allows throwing an exception
  * urequests: reads the content-length from the header and uses the length when issuing the read for the body
  * hmac: changed the update to allow binary keys; does not do a copy when asking for the digest

The shade_controller module uses 2 classes to drive components:
  * motor.py
  * ina219.py: supports putting the INA219 to sleep.  No floating point operations.
  
  
  Futures: a simple thing class that does not require any hardware other than the LED's on a standard development board.
