def reset():
    """Stops webrepl before doing a reset"""
    import webrepl, machine, utime
    print("Resetting...")
    webrepl.stop()
    utime.sleep_ms(3000)
    machine.reset()

def showf(fname='pstate.txt'):
    with open(fname) as f:
        s=f.read()
    print(s)
