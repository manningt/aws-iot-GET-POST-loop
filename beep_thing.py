from base_thing import Base_thing
class Beep_thing(Base_thing):
    """ An example class showing how to add AWS-IOT parameters (desired state variables) 
        as operators that do something.
        """
    def __init__(self):
        super().__init__()
        # if a parameter is not restored from persistence, then add it with a default value
        if 'beep' not in self._current_state['params']:
            self._current_state['params']['beep'] = 0
        self._operations['beep'] = self._beep
        self._test_operations['child'] = self._test_child

    @property
    def id(self):
        try:
            import socket
        except:
            return "id-1"
        return socket.gethostname()

    def time(self):
        import time
        t = time.gmtime()
        # had to make isdst unknown to get correct timestamp
        tc = (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], -1)
        return time.mktime(tc)

    def _beep(self):
        self._current_state['params']['beep'] = self._shadow_state['state']['desired']['beep']
        from utime import sleep_ms
        for _ in range(self._current_state['params']['beep']):
            print('\a')
            sleep_ms(300)
        return "done: beeped {} times".format(self._current_state['params']['beep'])

    def _test_child(self):
        return "pass: test 'child'"

