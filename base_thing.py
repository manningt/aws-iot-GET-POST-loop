import logging
logger = logging.getLogger(__name__)

class BaseThing(object):
    """ A base class for AWS-IOT things
        This class holds state info for a device; The variables in state info control the device's behavior.
        The expected steps to use this class are:
         * instantiate the class
         * update the shadow state: this invokes any actions the device should perform on a state change
         * read the reported state: this adds coditions (temperature, battery voltage, etc) to the reported_state
        The inherited class should:
         * Implement a property to return an ID.  The ID will be used to get the AWS-IOT shadow
         * Provide a function to return a timestamp. Getting the current date/time can be device specific
         * Register additional operations/functions by adding them to the _operations and _test_operations dictionaries
        """
    def __init__(cls):
        """
            This method can be overridden by a child class to add more device specific operations and hardware initialization
            super().__init__ should be called early in the child class to set up the data structures
        """
        # the following dictionaries are functions that are called when a desired state variable changes.
        # functions can be added by the child class __init__ function for device specific operations
        cls._operations = {'test': cls._dispatch_test, 'test_param': cls._dispatch_test}
        cls._test_operations = {'none' : cls._test_none}

        # the following dictionary are identify functions to measure conditions
        # functions can be added by the child class __init__ function for device specific operations
        # The following example reports free memory if it changes by 2K or every hour, which ever comes first
        #    self._conditions['freeMemory'] = {'get': self.get_mem_free, 'threshold' : 2048, 'interval': 3600}
        cls._conditions = {}

        cls._shadow_state = {}  # holds the shadow state obtained from AWS-IOT; read (not modified) by the controller
        cls._reported_state = {}  # holds the state to be posted to the shadow; written by the controller

        """ _current_state should reflect the device's REAL state.
            * _current_state parameters start with defaults, which get over-written by the values read from persistent store
            * not all shadow_state parameters need to be persisted
            * sleep is persisted because it is used even if unable to get the desired values from AWS
            * the sleep default of 0 allows recovery if the network is not reachable when the device is initialized
            * test and test_param are persisted so they can be compared to the new desired state
            """
        cls._current_state = {'params': {'sleep': 0, 'test': 'none', 'test_param': 0}, 'history' : []}
        cls._restored_state = cls._restore_state()
        if len(cls._restored_state) > 0:
            for key in cls._restored_state['params']:
                cls._current_state['params'][key] = cls._restored_state['params'][key]
        cls._has_history = 'history' in cls._restored_state and len(cls._restored_state['history']) > 0
        cls._timestamp = None

    """
    @property and prop.setter decorators were not used because I couldn't get the call to super().prop to work
    in the derived class.  Hence for reported & shadow state, the prop = property(get, set) mechanism was used.
    """
    def _reported_state_get(cls):
        """
            Adds conditions to the reported state.
                 Note: Desired value changes are added to _reported_state in shadow_state.setter
            This method can be overridden by a child class to report additional device specific state
            super()._reported_state_get should be called by the child class to return the base_class reported state

            Since getting conditions may read hardware, e.g. I2C, then call as few times as possible to save power
        """
        for condition in cls._conditions:
            if 'get' in cls._conditions[condition]:
                logger.debug("Checking condition: %s", condition)
                # if condition already in reported state, then check threshold crossing & update interval
                if 'state' in cls._shadow_state and 'reported' in cls._shadow_state['state']\
                        and condition in cls._shadow_state['state']['reported']:
                    if 'threshold' in cls._conditions[condition]:
                        current_value = cls._conditions[condition]['get']()
                        delta = cls._shadow_state['state']['reported'][condition] - current_value
                        if (abs(delta) > cls._conditions[condition]['threshold']):
                            cls._reported_state[condition] = current_value
                            logger.debug("Updating %s due to threshold %d; delta: %d", condition, \
                                      cls._conditions[condition]['threshold'], delta)
                    if not condition in cls._reported_state and \
                            'interval' in cls._conditions[condition] and \
                            'metadata' in cls._shadow_state:
                        if cls._timestamp is not None:
                            previous_report_timestamp = cls._shadow_state['metadata']['reported'][condition]['timestamp']
                            delta_time = cls._timestamp - previous_report_timestamp
                            if delta_time > cls._conditions[condition]['interval']:
                                logger.debug("Updating %s due to interval %s; delta_time: %s", condition, \
                                          cls._conditions[condition]['interval'], delta_time)
                                cls._reported_state[condition] = cls._conditions[condition]['get']()
                        else:
                            logger.warning("no cls._timestamp when evaluating condition interval")
                else:
                    cls._reported_state[condition] = cls._conditions[condition]['get']()
            else:
                logger.warning("no get function for condition {}". format(condition))

        return cls._reported_state

    reported_state = property(_reported_state_get)

    def _shadow_state_get(cls):
        """
             This method is provided for completeness, and is not used in normal operation
         """
        return cls._shadow_state

    def _shadow_state_set(cls, shadow_state):
        """ Compares shadow state received from AWS-IOT to the current state.  If a variable (current vs desired)
            doesn't match, a function from the _operations dictionary will be called.  After the operation is complete,
            the updated current state and command history is persisted.  The reported state is also updated.
            This method can be overridden by a child class to perform additional state checks before calling
            super()._shadow_state_set
        """
        cls._shadow_state = shadow_state
        history_updated = False
        # the sleep parameter does not trigger an operation; its updated so it will be persisted
        if 'sleep' in shadow_state['state']['desired']:
            cls._current_state['params']['sleep'] = shadow_state['state']['desired']['sleep']

        # parameters that trigger an operation, such as test, are updated in the function that processes them.
        if cls._has_history and cls._restored_state['history'][0]['done'] != 1:
            # The operation didn't finished, so update the status to be reflected in an updated report
            cls._current_state['history'][0]['done'] = 1
            cls._current_state['history'][0]['status'] = "Error: state change: {}: {} failed before completion"\
                .format(cls._restored_state['history'][0]['op'], cls._restored_state['history'][0]['value'])
            history_updated = True
        else:
            for key in cls._operations:
                desired_unequal_current = False
                if key in shadow_state['state']['desired']:
                    desired_unequal_current = shadow_state['state']['desired'][key] != cls._current_state['params'][key]
                # if the persisted state is unknown, the operation is not performed
                if cls._current_state['params'][key] == "unknown":
                    desired_unequal_current = False

                # check the metadata timestamp to prevent an operation being performed twice
                timestamp_changed = True
                desired_timestamp = 0
                if cls._has_history and key == cls._restored_state['history'][0]['op']:
                    if 'metadata' in shadow_state and key in shadow_state['metadata']['desired']:
                        desired_timestamp = shadow_state['metadata']['desired'][key]['timestamp']
                    if desired_timestamp == cls._restored_state['history'][0]['timestamp']:
                        timestamp_changed = False

                if desired_unequal_current and timestamp_changed:
                    cls._current_state['history'].insert(0, {'done': 0, \
                                        'op': key, \
                                        'value': shadow_state['state']['desired'][key], \
                                        'timestamp' : desired_timestamp})
                    logger.debug("Performing operation: %s", key)
                    cls._current_state['history'][0]['status'] = cls._operations[key]()
                    logger.info("Operation '%s' status: %s", key, cls._current_state['history'][0]['status'])
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
            # maintain a history of 2 operations by appending the restored (previous) operation
            if cls._has_history:
                cls._current_state['history'].append(cls._restored_state['history'][0])
            cls._persist_state()

        # generate reported state
        if history_updated:
            cls._reported_state['status'] = cls._current_state['history'][0]['status']
        for key, value in shadow_state['state']['desired'].items():
            # Note: On start-up, there is no reported dictionary
            # This routine only reports changed values in order to not have the shadow version keep incrementing
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

    shadow_state = property(_shadow_state_get, _shadow_state_set)

    def connect(cls):
        """ This method can be overridden by a child class to invoke device specific networking code to connect to
            the network.
        """
        return True, None

    def sleep(cls, msg=None):
        """ This method can be overridden by a child class to invoke device specific power saving modes.
        """
        from utime import sleep
        if msg is not None:
            logger.warning("%s", msg)
        logger.info(" ... Going to sleep for %s seconds.", cls._current_state['params']['sleep'])
        sleep(cls._current_state['params']['sleep'])

    def get_aws_iot_cfg(cls):
        """ This method can be overridden by a child class to retrieve from a device specific persistent store.
        """
        return cls._get_cfg_info("aws_iot_cfg.txt")

    def get_aws_credentials(cls):
        """ This method SHOULD be overridden by a child class to retrieve from a SECURE persistent store.
        """
        return cls._get_cfg_info("aws_credentials.txt")

    def _restore_state(cls):
        """ This method must be overridden by a child class to write to device specific persistent storage
        """
        logger.error("_restore_state should be overridden by the child.")
        return {}

    def _persist_state(cls):
        """ This method must be overridden by a child class to read from device specific persistent storage
        """
        logger.error("_persist_state should be overridden by the child.")

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
        """ a dummy test, but it can be used to verify tests are being dispatched
            """
        return "pass: test 'none'"

    def _get_cfg_info(cls, filename):
        import ujson
        try:
            with open(filename) as f:
                cfg_info = ujson.load(f)
            return cfg_info
        except OSError as e:
            e_str = str(e)
            logger.error("In get_cfg_info from filename: %s   Exception: %s", filename, e_str)
            return None

