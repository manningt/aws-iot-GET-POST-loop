from base_thing import BaseThing
class SignalThing(BaseThing):
    """ An example class showing how to add AWS-IOT parameters (desired state variables) as operators that do something.
        To use this class, a thing and shadow desired state should be created first in AWS IoT
    """
    def __init__(self):
        self._PERSIST_FILENAME = "./thing_state.txt"
        super().__init__()
        # if a parameter is not restored from persistence, then add it with a default value
        if 'signal' not in self._current_state['params']:
            self._current_state['params']['signal'] = 0
        # add signal operations to the base class
        self._operations['signal'] = self._signal
        self._test_operations['child'] = self._test_child

    @property
    def id(self):
        """ returns a thing-shadow ID to be used when generating the AWS request for shadow state """
        my_id = "id-1"
        try:
            # the following works for python3 but not micropython
            import socket
            my_id = socket.gethostname()
        except: pass
        return my_id

    def time(self):
        """ returns a GMT timestamp to be used when generating the AWS request """
        import time
        t = time.gmtime()
        # had to make 'isdst' unknown to get a GMT timestamp
        tc = (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], -1)
        return time.mktime(tc)

    def _restore_state(cls):
        """ Restores _current_state from a file.
        """
        try:
            import ujson
        except:
            import json as ujson

        try:
            with open(cls._PERSIST_FILENAME) as f:
                return ujson.load(f)
        except OSError:
            print("Warning (restore_state): file '{}' does not exist".format(cls._PERSIST_FILENAME))
            return {}

    def _persist_state(cls):
        """ Persists _current_state from a file.
        """
        try:
            import ujson
        except:
            import json as ujson
        # there is no ujson.dump, only ujson.dumps in micropython
        try:
            with open(cls._PERSIST_FILENAME, "w") as f:
                f.write(ujson.dumps(cls._current_state))
        except OSError:
            print("Error: persisting state failed.")

    def _signal(self):
        """ causes the device to signal (in the case of Unix, beep the terminal) for the number of times specified
        by the signal parameter in the desired state
        """
        self._current_state['params']['signal'] = self._shadow_state['state']['desired']['signal']
        from utime import sleep_ms
        for _ in range(self._current_state['params']['signal']):
            print('beep\a')
            sleep_ms(300)
        return "done: signaled {} times".format(self._current_state['params']['signal'])

    def _test_child(self):
        # a dummy test just to demonstrate the addition of device/child specific tests
        return "pass: test 'child'"

    # Uncomment the following if extending the shadow_state getter/setter:
    # def _shadow_state_get(self):
    #     return super()._shadow_state_get()
    #
    # def _shadow_state_set(self, shadow_state):
    #     super()._shadow_state_set(shadow_state)
    #
    # shadow_state = property(_shadow_state_get, _shadow_state_set)