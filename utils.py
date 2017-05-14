def reset():
    """Stops webrepl before doing a reset"""
    import webrepl, machine, utime
    print("Resetting...")
    webrepl.stop()
    # a work-around for: https://github.com/micropython/micropython/issues/2635
    # there is no rtc.init, so trying to set up the timer - this didn't work either
    rtc = machine.RTC()
    rtc.irq(trigger=rtc.ALARM0, wake=machine.DEEPSLEEP)
    rtc.alarm(rtc.ALARM0, 5000)
    utime.sleep_ms(3000)
    machine.reset()

def showf(fname='pstate.txt'):
    with open(fname) as f:
        s=f.read()
    print(s)
