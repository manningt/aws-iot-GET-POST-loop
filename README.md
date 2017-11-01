# aws-iot-GET-POST-loop
Micropython code which uses the AWS-IOT REST API to GET/POST device state info.

There are 4 primary modules:
  * aws_thing_loop: instances a thing, GETs the AWS shadow state for the thing, and POSTs the reported state to AWS
  * base_thing: provides the functions every thing should have: set the shadow_state, get the reported state, get ID, get the current time
  * a thing: inherits from base_thing and adds device specific capabilitie.  There are 3 examples:
    * signal_thing_unix: when the integer value of the 'signal' variable in desired state is changed, the terminal where the script is running will beep
    * signal_thing_esp: similar to signal_thing_unix, an ESP8266 will flash its LED.  On the 8266, it requires pin 16 to be connected to the reset pin for deep sleep to work
    * shade_controller: runs on an ESP; controls a motor to position a shade
  * main.py: calls aws_thing_loop.main()  In the micropython environment, main.py is run after booting.
  
The use case is an IoT application where the device sleeps most of the time (to save battery) and retrieve commands from the cloud when the device wakes up. A motorized window treatment which uses a processor to control a motor and position a shade/curtain is an example. It wakes up and retrieves the desired position every few minutes. If the desired position is different than the current position, the processor controls the motor to change the position to the desired position.

A more detailed description of how to setup and run the software is at: [hackster.io](https://www.hackster.io/user3282664/micropython-to-aws-iot-cc1c20)

aws_thing_loop requires:
* [aws-signature-iot-python](https://github.com/manningt/aws-signature-iot-python)
* [urequests](https://github.com/manningt/micropython-lib/tree/urequest-with-content-length/urequests) (modified): reads the content-length from the header and uses the length when issuing the read for the body.  The modification handles the situation where AWS does not close the socket after the GET (not compliant with HTTP1.0)
* [hmac_ltd](https://github.com/manningt/aws-signature-iot-python/hmac_ltd.py): a modified version of hmac which allows binary keys

## An example using custom hardware: a window shade controller
### Motorized Shade Overview
The overarching goal of this project is to save energy by using automated/motorized honeycomb shades:
* Honeycomb shades have air pockets built into their fabric that provide insulation. These air pockets help keep your house cooler in the summer and warmer in the winter.
* Lowering shades in the summer prevents the sun’s harsh rays from heating up a room (and causing an air conditioner to work overtime), while raising them in the winter captures heat from those rays.
* Motorization allows automation:  all the shades in a building can be open/closed based on policies:
  * time: sunrise/sunset
  * weather: temperature and sunlight
  * occupancy and personal preference
Motorized shades are available from major window treatment vendors.  However, they are expensive - adding $200 per shade - which makes the ROI impractical.  And they require a bridge, which means managing another network in addition to the WiFi network.
The intention is to lower the cost of motorization by offering open-source hardware & software.
### Circuit Board Overview
The circuit board is dimensioned to fit inside the shade housing.  It has connectors for the following:
* LiON battery input
* input from a solar cell or permanent power source
* driving a DC motor that connects to the shade's shaft.  As the shaft rotates, cords raise or lower the shade
* A second DC motor, so that top-down, bottom-up shades can be motorized.
* Debug connector for resetting, flashing and debugging.
* Touch sensors for manual open/close control (not implemented yet)

The circuit board has the following components:
* ESP32 microprocessor module with integrated WiFi and BLE and memory
* MCP73871 solar powered Lion battery charger
* INA219 a current sensor that is used to monitor battery state and motor currents
* DRV8871 motor drivers with Internal Current Sense
* 3.3V regulator and 12V booster; 12V is used to drive the motor
* ATECC508 for secure key and/or certificate storage
* LM75B temperature sensor, so the temperature at the actual window location is known.

The circuit board schematic and layout is at: [![motor_driver_with_sensors-v3 by MyOrg a7f8b2001762c006 - Upverter](https://upverter.com/MyOrg/a7f8b2001762c006/motor_driver_with_sensors-v3/embed_img/15049524790000/)](https://upverter.com/MyOrg/a7f8b2001762c006/motor_driver_with_sensors-v3/#/)

The PCB can be ordered here: [oshpark](https://oshpark.com/shared_projects/A6QuHnHe)

2 modules used by the shade_controller module are:
  * motor.py
  * ina219.py: supports the ina219 current sensor. Supports the INA219 sleep mode and does not use floating point operations.
  
