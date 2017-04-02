class INA219(object):

    _INA219_REG_CONFIG         = 0x00
    _INA219_REG_SHUNTVOLTAGE   = 0x01
    _INA219_REG_BUSVOLTAGE     = 0x02
    in_standby_when_initialized = False

    def __init__(self, i2c=None, i2c_addr=64):
        self.i2c = i2c
        self.i2c_addr = i2c_addr
        
        value = bytearray(2)
        self.i2c.readfrom_mem_into(self.i2c_addr, self.__INA219_REG_CONFIG, value)
        if ((value[1] & 0x07) == 0):
            self.in_standby_when_initialized = True;

    def start(self, BRNG=1,PG=3,BADC=3,SADC=3,MODE=7):
        #power on reset cfg setting of 0x399F is equal to the default start arguments
        calstr = bytearray(2)
        calstr[0] = ((BRNG&0x01)<<5)+((PG&0x03)<<3)+((BADC>>1)&0x07)
        calstr[1] = ((BADC&0x01)<<7)+((SADC&0x0F)<<3)+(MODE&0x07)
        self.i2c.writeto_mem(self.i2c_addr, self.__INA219_REG_CONFIG , calstr)

    def get_current_ma(self):
        value = bytearray(2)
        self.i2c.readfrom_mem_into(self.i2c_addr, self._INA219_REG_SHUNTVOLTAGE , value)
        if ((value[0] & 0x80) > 0):
            # negative number, so sign extend
            Vshunt = -((~value[0]<<8) + 65536 + ~value[1] + 256) #-1 <- may be off by one, but since returning mA, it doesn't matter
        else:
            Vshunt = (value[0]<<8) + value[1]
        Ishunt = (Vshunt * 205) >> 11   #multiply by Rshunt (0.1 ohms).  Done by ultiplying by 205 and shifting
        return Ishunt

    def get_bus_mv(self):
        value = bytearray(2)
        self.i2c.readfrom_mem_into(self.i2c_addr, self._INA219_REG_BUSVOLTAGE , value)
        Vbus = (value[0]<<8) + (value[1] & 0xF8) #mask out CNVR & OVF
        Vbus = Vbus >> 1  #shift right to remove the low 3 bits, but then multiply by 4 (shift left by 2)
        return Vbus

    def stop(self):
        self.start(MODE=0)

    def reset(self):
        self.i2c.writeto_mem(self.i2c_addr, self._INA219_REG_CONFIG , b'\x80\x00')
