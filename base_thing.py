class Base_thing(object):
    ''
    """ A base class for AWS-IOT things
        This class holds state info for a device.  The state info controls the devices behavior.
        The expected procedure to be performed by the instantiator of this class are:
         * instantiate the class
         * update the shadow state: this invokes any actions the device should perform on a state change
         * read the reported state: this adds coditions (temperature, battery voltage, etc) to the reported_state
        """

    def __init__(cls):
        cls._PERSIST_FILENAME = "./thing_state.txt"
        """ the following dictionary are functions that are called when a desired state variable changes:
            the dictionary can be added to by overloading for device specific features
            """
        cls._operations = {'test': cls._dispatch_test, 'test_param': cls._dispatch_test}
        cls._test_operations = {'none' : cls._test_none}

        cls._shadow_state = {}  # holds the shadow state obtained from AWS-IOT; read (not modified) by the controller
        cls._reported_state = {}  # holds the state to be posted to the shadow; written by the controller

        """ _current_state is the device's REAL state.
            - _current_state parameters start with defaults, which get over-written by the values read from persistent store
            - not all shadow_state parameters need to be persisted
            - sleep is persisted because it is used even if unable to get the desired values from AWS
            - the sleep default of 0 allows recovery if the network is not reachable when the device is initialized
            - test and test_param are persisted so they can be compared to the new desired state
            """
        cls._current_state = {'params': {'sleep': 0, 'test': 'none', 'test_param': 0}, 'history' : []}
        cls._restored_state = cls._restore_state()
        if len(cls._restored_state) > 0:
            for key in cls._restored_state['params']:
                cls._current_state['params'][key] = cls._restored_state['params'][key]
        cls._has_history = 'history' in cls._restored_state and len(cls._restored_state['history']) > 0

    @property
    def reported_state(cls):
        return cls._reported_state

    @property
    def shadow_state(cls):
        return cls._shadow_state

    @shadow_state.setter
    def shadow_state(cls, shadow_state):
        cls._shadow_state = shadow_state
        history_updated = False

        # the following state parameters do not trigger an operation
        # they are updated so they'll be persisted after the operation is done
        if 'sleep' in shadow_state['state']['desired']:
            cls._current_state['params']['sleep'] = shadow_state['state']['desired']['sleep']

        # other variables that trigger an operation, such as test, are updated in the function that processes them.
        if cls._has_history and cls._restored_state['history'][0]['done'] != 1:
            # The operation didn't finished, so update the status to be reflected in an updated report
            cls._current_state['history'].insert(0, cls._restored_state['history'][0])
            cls._current_state['history'][0]['done'] = 1
            cls._current_state['history'][0]['status'] = "Error: state change: {}: {} failed before completion"\
                .format(cls._restored_state['history'][0]['op'], cls._restored_state['history'][0]['value'])
            history_updated = True
        else:
            for key in cls._operations:
                shadow_state_changed = False
                if key in shadow_state['state']['desired']:
                    shadow_state_changed = shadow_state['state']['desired'][key] != cls._current_state['params'][key]
                # if the persisted state is unknown, the operation is not performed
                if cls._current_state['params'][key] == "unknown":
                    shadow_state_changed = False

                if not cls._has_history:
                    timestamp_changed = True
                elif key not in shadow_state['metadata']['desired']:
                    timestamp_changed = True
                else:
                    timestamp_changed = shadow_state['metadata']['desired'][key] != cls._restored_state['history'][0]['timestamp']

                if shadow_state_changed and timestamp_changed:
                    cls._current_state['history'].insert(0, {'done': 0, \
                                        'op': key, \
                                        'value': shadow_state['state']['desired'][key], \
                                        'timestamp' : shadow_state['metadata']['desired'][key]['timestamp']})
                    cls._current_state['history'][0]['status'] = cls._operations[key]()
                    cls._current_state['history'][0]['done'] = 1
                    history_updated = True
                    # dispatch only one parameter per update
                    break

        # persist parameter changes, if any
        state_change = False
        if len(cls._restored_state) == 0 or history_updated:
            state_change = True
        else:
            for key in cls._current_state['params']:
                if key not in cls._restored_state['params'] or cls._current_state['params'][key] != cls._restored_state['params'][key]:
                    state_change = True
                    break

        if state_change:
            # maintain a history of 1 or 2 operations
            if cls._has_history:
                cls._current_state['history'].append(cls._restored_state['history'][0])
            cls._persist_state()

        # generate reported state
        if history_updated:
            cls._reported_state['status'] = cls._current_state['history'][0]['status']
        for key, value in shadow_state['state']['desired'].items():
            # On start-up, there is no reported dictionary
            # only report changed values in order to not have the shadow version keep incrementing
            if key in cls._current_state['params']:
                # persisted keys report current_state
                if 'reported' not in shadow_state['state'] or \
                                key not in shadow_state['state']['reported'] or \
                                shadow_state['state']['reported'][key] != cls._current_state['params'][key]:
                    cls._reported_state[key] = cls._current_state['params'][key]
            else:
                # non-persisted keys report/echo shadow_state
                if 'reported' not in shadow_state['state'] or \
                                key not in shadow_state['state']['reported'] or \
                                shadow_state['state']['reported'][key] != value:
                    cls._reported_state[key] = value

    def connect(cls):
        """
            This method is to be overloaded by the inherited class with device specific code
        """
        return True, None

    def sleep(cls, msg=None):
        from utime import sleep
        if msg is not None:
            print(msg, end='')
        print(" ... Going to sleep for {0} seconds.".format(cls._current_state['params']['sleep']))
        sleep(cls._current_state['params']['sleep'])

    def _restore_state(cls):
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

    def _dispatch_test(cls):
        """ calls one of the test functions based on the string in the shadow_state 'test' variable
            Inputs: shadow_state
            Returns: status string received from the test
            """
        cls._current_state['params']['test_param'] = 0
        if 'test_param' in cls._shadow_state['state']['desired']:
            cls._current_state['params']['test_param'] = cls._shadow_state['state']['desired']['test_param']
        cls._current_state['params']['test'] = cls._shadow_state['state']['desired']['test']
        if cls._current_state['params']['test'] in cls._test_operations:
            status = cls._test_operations[cls._current_state['params']['test']]()
        else:
            status = "Unrecognized test: " + cls._current_state['params']['test']
        return status

    def _test_none(cls):
        return "pass: test 'none'"
