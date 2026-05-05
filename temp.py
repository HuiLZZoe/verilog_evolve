from myhdl import block, always_seq

import time

@block
def inc(count, enable, clock, reset):
    """ Incrementer with enable.

    count -- output
    enable -- control input, increment when 1
    clock -- clock input
    reset -- asynchronous reset input
    """
    
    @always_seq(clock.posedge, reset=reset)
    def seq():
        if enable:
            count.next = count + 1

    return seq

from myhdl import Signal, ResetSignal, modbv, intbv

# from inc import inc

# def convert_inc(hdl):
#     """Convert inc block to Verilog or VHDL."""

#     m = 8

#     count = Signal(modbv(0)[m:])
#     enable = Signal(bool(0))
#     clock  = Signal(bool(0))
#     reset = ResetSignal(0, active=0, isasync=True)

#     inc_1 = inc(count, enable, clock, reset)

#     time_now = str(time.time())

#     inc_1.convert(hdl=hdl, path='./temp', name = 'inc_' + time_now, )


# convert_inc(hdl='Verilog')
# convert_inc(hdl='VHDL')

# def convert_inc():
#     count = Signal(intbv(0)[8:])   # Assuming a width of 8 bits for the count
#     enable = Signal(bool(0))
#     clock = Signal(bool(0))
#     reset = ResetSignal(0, active=1, isasync=True)
    
#     inc_inst = inc(count, enable, clock, reset)
#     inc_inst.convert(hdl='Verilog')

# convert_inc()

def convert_to_verilog():
    # Creating a dummy clock, enable, and reset signal for conversion purposes
    clock = Signal(bool(0))
    enable = Signal(bool(0))
    reset = ResetSignal(0, active=1, isasync=True)
    count = intbv(0)[8:]  # Assume a 8-bit counter for simplicity

    # Instantiating the block
    inc_inst = inc(count, enable, clock, reset)

    # Converting to Verilog
    inc_inst.convert(hdl='Verilog', initial_values=True)

if __name__ == "__main__":
    convert_to_verilog()