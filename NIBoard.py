from __future__ import division, unicode_literals, print_function, absolute_import
from labscript_utils import PY2
if PY2:
    str = unicode

import numpy as np
from labscript_devices import runviewer_parser
from labscript import IntermediateDevice, AnalogOut, DigitalOut, AnalogIn, bitfield, config, LabscriptError, set_passed_properties, DAQCounter
import labscript_utils.h5_lock, h5py
import labscript_utils.properties
from labscript_utils.numpy_dtype_workaround import dtype_workaround

class NIBoard(IntermediateDevice):
    allowed_children = [AnalogOut, DigitalOut, AnalogIn, DAQCounter]
    n_analogs = 4
    n_digitals = 32
    digital_dtype = np.uint32
    clock_limit = 500e3 # underestimate I think.
    description = 'generic_NI_Board'

    @set_passed_properties(property_names = {
        "device_properties":["acquisition_rate", "MAX_name"]}
        )
    def __init__(self, name, parent_device, clock_terminal, MAX_name=None, acquisition_rate=0):
        IntermediateDevice.__init__(self, name, parent_device)
        self.acquisition_rate = acquisition_rate
        self.clock_terminal = clock_terminal
        self.MAX_name = name if MAX_name is None else MAX_name
        self.BLACS_connection = self.MAX_name

    def add_device(self,output):
        # TODO: check there are no duplicates, check that connection
        # string is formatted correctly.
        IntermediateDevice.add_device(self,output)

    def convert_bools_to_bytes(self,digitals):
        """converts digital outputs to an array of bitfields stored
        as self.digital_dtype"""
        outputarray = [0]*self.n_digitals
        for output in digitals:
            port, line = output.connection.replace('port','').replace('line','').split('/')
            port, line  = int(port),int(line)
            if port > 0:
                raise LabscriptError('Ports > 0 on NI Boards not implemented. Please use port 0, or file a feature request at redmine.physics.monash.edu.au/labscript.')
            outputarray[line] = output.raw_output
        bits = bitfield(outputarray,dtype=self.digital_dtype)
        return bits

    def generate_code(self, hdf5_file):
        IntermediateDevice.generate_code(self, hdf5_file)
        analogs = {}
        digitals = {}
        inputs = {}
        counters = {}
        for device in self.child_devices:
            if isinstance(device,AnalogOut):
                analogs[device.connection] = device
            elif isinstance(device,DigitalOut):
                digitals[device.connection] = device
            elif isinstance(device,AnalogIn):
                inputs[device.connection] = device
            elif isinstance(device,DAQCounter):
                counters[device.connection] = device
            else:
                raise Exception('Got unexpected device.')

        clockline = self.parent_device
        pseudoclock = clockline.parent_device
        times = pseudoclock.times[clockline]

        ## Analog out
        analog_out_table = np.empty((len(times),len(analogs)), dtype=np.float32)
        analog_connections = list(analogs.keys())
        analog_connections.sort()
        analog_out_attrs = []
        for i, connection in enumerate(analog_connections):
            output = analogs[connection]
            if any(output.raw_output > 10 )  or any(output.raw_output < -10 ):
                # Bounds checking:
                raise LabscriptError('%s %s '%(output.description, output.name) +
                                  'can only have values between -10 and 10 Volts, ' +
                                  'the limit imposed by %s.'%self.name)
            analog_out_table[:,i] = output.raw_output
            analog_out_attrs.append(self.MAX_name +'/'+connection)

        ## Analog in
        input_connections = list(inputs.keys())
        input_connections.sort()
        input_attrs = []
        acquisitions = []
        for connection in input_connections:
            input_attrs.append(self.MAX_name+'/'+connection)
            for acq in inputs[connection].acquisitions:
                acquisitions.append((connection,acq['label'],acq['start_time'],acq['end_time'],acq['wait_label'],acq['scale_factor'],acq['units']))
        # The 'a256' dtype below limits the string fields to 256
        # characters. Can't imagine this would be an issue, but to not
        # specify the string length (using dtype=str) causes the strings
        # to all come out empty.
        acquisitions_table_dtypes = dtype_workaround([('connection','a256'), ('label','a256'), ('start',float),
                                     ('stop',float), ('wait label','a256'),('scale factor',float), ('units','a256')])
        acquisition_table= np.empty(len(acquisitions), dtype=acquisitions_table_dtypes)
        for i, acq in enumerate(acquisitions):
            acquisition_table[i] = acq

        ## Digital out
        digital_out_table = []
        if digitals:
            digital_out_table = self.convert_bools_to_bytes(list(digitals.values()))

        ## Counter
        counter_connections = counters.keys()
        counter_connections.sort()
        counter_attrs = []
        CPT_attrs = [] ##EE2
        trig_attrs = [] ##EE2
        counter_acquisitions = []
        for connection in counter_connections:
            counter_attrs.append(self.MAX_name+'/'+connection)
            CPT_attrs.append(self.MAX_name+'/'+counters[connection].CPT_connection) ##EE2
            trig_attrs.append(self.MAX_name+'/'+counters[connection].trigger) ##EE2
            for acq in counters[connection].acquisitions:
                counter_acquisitions.append((connection,counters[connection].CPT_connection,counters[connection].trigger,acq['label'],acq['start_time'],acq['end_time'],acq['sample_freq'],acq['wait_label']))
        # The 'a256' dtype below limits the string fields to 256
        # characters. Can't imagine this would be an issue, but to not
        # specify the string length (using dtype=str) causes the strings
        # to all come out empty.
        counter_acquisitions_table_dtypes = dtype_workaround([('connection','a256'),('CPT_connection','a256'), ('trigger','a256'),('label','a256'), ('start',float),
                                                              ('stop',float), ('sample freq',float),('wait label','a256')])
        counter_acquisition_table= np.empty(len(counter_acquisitions), dtype=counter_acquisitions_table_dtypes)
        for i, acq in enumerate(counter_acquisitions):
            counter_acquisition_table[i] = acq
            print(acq)

        ## Put attributes in hdf5 file
        grp = self.init_device_group(hdf5_file)
        if all(analog_out_table.shape): # Both dimensions must be nonzero
            grp.create_dataset('ANALOG_OUTS',compression=config.compression,data=analog_out_table)
            self.set_property('analog_out_channels', ', '.join(analog_out_attrs), location='device_properties')
        if len(digital_out_table): # Table must be non empty
            grp.create_dataset('DIGITAL_OUTS',compression=config.compression,data=digital_out_table)
            self.set_property('digital_lines', '/'.join((self.MAX_name,'port0','line0:%d'%(self.n_digitals-1))), location='device_properties')
        if len(acquisition_table): # Table must be non empty
            grp.create_dataset('ACQUISITIONS',compression=config.compression,data=acquisition_table)
            self.set_property('analog_in_channels', ', '.join(input_attrs), location='device_properties')
        if len(counter_acquisition_table): # Table must be non empty
            counter_dataset = grp.create_dataset('COUNTER_ACQUISITIONS',compression=config.compression,data=counter_acquisition_table)
#            counter_dataset2 = grp.create_dataset('COUNTER_ACQUISITIONS2',compression=config.compression,data=counter_acquisition_table)
            grp.attrs['counter_channels'] = ', '.join(counter_attrs)
            grp.attrs['cpt_channels'] = ', '.join(CPT_attrs) ##EE2
            grp.attrs['trig_channels'] = ', '.join(trig_attrs) ##EE2
            grp.attrs['counter_acquisition_rate'] = self.acquisition_rate ## Emily edit: need to add this, what is it doing?
        # TODO: move this to decorator (requires ability to set positional args with @set_passed_properties)
        self.set_property('clock_terminal', self.clock_terminal, location='connection_table_properties')


@runviewer_parser
class RunviewerClass(object):
    num_digitals = 32

    def __init__(self, path, device):
        self.path = path
        self.name = device.name
        self.device = device

        # We create a lookup table for strings to be used later as dictionary keys.
        # This saves having to evaluate '%d'%i many many times, and makes the _add_pulse_program_row_to_traces method
        # significantly more efficient
        self.port_strings = {}
        for i in range(self.num_digitals):
            self.port_strings[i] = 'port0/line%d'%i

    def get_traces(self, add_trace, clock=None):
        if clock is None:
            # we're the master pseudoclock, software triggered. So we don't have to worry about trigger delays, etc
            raise Exception('No clock passed to %s. The NI PCIe 6363 must be clocked by another device.'%self.name)

        # get the pulse program
        with h5py.File(self.path, 'r') as f:
            if 'ANALOG_OUTS' in f['devices/%s'%self.name]:
                analogs = f['devices/%s/ANALOG_OUTS'%self.name][:]
                analog_out_channels = labscript_utils.properties.get(f, self.name, 'device_properties')['analog_out_channels'].split(', ')
            else:
                analogs = None
                analog_out_channels = []

            if 'DIGITAL_OUTS' in f['devices/%s'%self.name]:
                digitals = f['devices/%s/DIGITAL_OUTS'%self.name][:]
            else:
                digitals = []

        times, clock_value = clock[0], clock[1]

        clock_indices = np.where((clock_value[1:]-clock_value[:-1])==1)[0]+1
        # If initial clock value is 1, then this counts as a rising edge (clock should be 0 before experiment)
        # but this is not picked up by the above code. So we insert it!
        if clock_value[0] == 1:
            clock_indices = np.insert(clock_indices, 0, 0)
        clock_ticks = times[clock_indices]

        traces = {}
        for i in range(self.num_digitals):
            traces['port0/line%d'%i] = []
        for row in digitals:
            bit_string = np.binary_repr(row,self.num_digitals)[::-1]
            for i in range(self.num_digitals):
                traces[self.port_strings[i]].append(int(bit_string[i]))

        for i in range(self.num_digitals):
            traces[self.port_strings[i]] = (clock_ticks, np.array(traces[self.port_strings[i]]))

        for i, channel in enumerate(analog_out_channels):
            traces[channel.split('/')[-1]] = (clock_ticks, analogs[:,i])

        triggers = {}
        for channel_name, channel in self.device.child_list.items():
            if channel.parent_port in traces:
                if channel.device_class == 'Trigger':
                    triggers[channel_name] = traces[channel.parent_port]
                add_trace(channel_name, traces[channel.parent_port], self.name, channel.parent_port)

        return triggers
