#!/usr/bin/python

"""
This script reads in the profile-raw file generated by Glulxe profiling,
and lists the ten most costly functions. (In terms of how much total time
was spent inside each function. If a function calls other functions, the
time spent in them is not charged to the parent; that is, a function
which does nothing but call other functions will be considered uncostly.)

Optionally, this script can also read the debug output of the Inform 6
compiler (or the assembly output), and use that to figure out the
names of all the functions that were profiled.

Using this script is currently a nuisance. The requirements:

- You must compile Glulxe with profiling (the VM_PROFILING compile-time
  option).
- (If you want function names) you should compile your Inform 6 source
  using the -k switch. This generates a "gameinfo.dbg" file.
- Run Glulxe, using the "--profile profile-raw" option. Play some of
  the game, and quit. This generates a data file called "profile-raw".
- Run this script, giving gameinfo.dbg and profile-raw as arguments.

To sum up, in command-line form:

% inform -G -k game.inf
% glulxe --profile profile-raw game.ulx
% python profile-analyze.py profile-raw gameinfo.dbg

You can also use the assembly output of the Inform compiler, which you
get with the -a switch. Save the output and use it instead of the debug
file:

% inform -G -a game.inf > game.asm
% glulxe --profile profile-raw game.ulx
% python profile-analyze.py profile-raw game.asm

The limitations:

The profiling code is not smart about VM operations that rearrange the
call stack. In fact, it's downright stupid. @restart, @restore,
@restoreundo, or @throw will kill the interpreter.

Inform's -k switch does not work correctly with game files larger than
16 megabytes.

Inform's -a switch does not display code for veneer functions, so if
you use that data, these will not be named; they will be listed as
"<???>". This is a particular nuisance because veneer functions are
often the most costly ones. (Therefore, you'll almost certainly want
to use -k.)

You can explore the profiling data in more detail by running the script
interactively:

% python -i profile-analyze.py profile-raw game.asm

After it runs, you'll be left at a Python prompt. The environment
will contain mappings called "functions" (mapping addresses to
function objects), and "function_names" (names to function objects).

>>> functions[0x3c]
<Function $3c 'Main__'>
>>> function_names['Main__']
<Function $3c 'Main__'>
>>> function_names['Main__'].dump()
Main__:
  at $00003c (line 0); called 1 times
  0.000067 sec (1 ops) spent executing
  6.273244 sec (117578 ops) including child calls

A Function object has lots of attributes:
 
  addr=INT:         The VM address of the function (in hex).
  hexaddr=STRING:   The VM address of the function in hex (as a string).
  name=STRING:      The function's name, or '<???>' if the function is
    not known (veneer functions).
  linenum=INT:      The line number of the function from the source code,
    or 0 if it is not derived from source (Main__, etc).
  call_count=INT:   The number of times the function was called.
  accel_count=INT:  The number of times the function was called with
    acceleration.
  total_time=FLOAT: The amount of time spent during all calls to the
    function (in seconds, as a floating-point value).
  total_ops=INT:    The number of opcodes executed during all calls to
    the function.
  self_time=FLOAT:  The amount of time spent during all calls to the
    function, excluding time spent in subcalls (functions called *by* the
    function).
  self_ops=INT:     The number of opcodes executed during all calls to
    the function, excluding time spent in subcalls.

(The self_time is the "cost" used for the original listing.)

Note that if a function does not make any function calls, total_time
will be the same as self_time (and total_ops the same as self_ops).

Two special function entries may be included. The function with address
"1" (which is not a legal Glulx function address) represents time spent
in @glk opcode calls. This will typically have a large self_time, 
because it includes all the time spent waiting for input.

The function with address "2" represents the time spent printing string
data (the @streamchar, @streamunichar, @streamnum, and @streamstr
opcodes).

(Both "1" and "2" represent time spent in the Glk library, but they
get there by different code paths.)

The function with the lowest address (ignoring "1" and "2") is the
top-level Main__() function generated by the compiler. Its total_time
is the running time of the entire program.

"""

import sys, os.path
import xml.sax
from struct import unpack

if (len(sys.argv) < 2):
    print "Usage: profile-analyze.py profile-raw [ gameinfo.dbg | game.asm ]"
    sys.exit(1)

profile_raw = sys.argv[1]
if (not os.path.exists(profile_raw)):
    print 'File not readable:', profile_raw
    sys.exit(1)

game_asm = None
if (len(sys.argv) >= 3):
    game_asm = sys.argv[2]
    if (not os.path.exists(game_asm)):
        print 'File not readable:', game_asm
        sys.exit(1)

special_functions = {
    1: 'glk', 2: 'streamout'
}
max_special_functions = max(special_functions.keys())

functions = None
sourcemap = None

class Function:
    def __init__(self, addr, hexaddr, attrs):
        self.addr = addr
        self.hexaddr = hexaddr
        val = special_functions.get(addr)
        if (val is None):
            self.name = '<???>'
            self.special = False
        else:
            self.name = '<@' + val + '>'
            self.special = True
        self.linenum = 0
        self.call_count =   int(attrs['call_count'])
        self.accel_count = 0
        if (attrs.has_key('accel_count')):
            self.accel_count = int(attrs['accel_count'])
        self.total_ops  =   int(attrs['total_ops'])
        self.total_time = float(attrs['total_time'])
        self.self_ops   =   int(attrs['self_ops'])
        self.self_time  = float(attrs['self_time'])
        
    def __repr__(self):
        return '<Function $' + self.hexaddr + ' ' + repr(self.name) + '>'

    def dump(self):
        print '%s:' % (self.name,)
        val = ''
        if (self.accel_count):
            val = ' (%d accelerated)' % (self.accel_count,)
        print '  at $%06x (line %d); called %d times%s' % (self.addr, self.linenum,self.call_count,val)
        print '  %.6f sec (%d ops) spent executing' % (self.self_time, self.self_ops)
        print '  %.6f sec (%d ops) including child calls' % (self.total_time, self.total_ops)

class ProfileRawHandler(xml.sax.handler.ContentHandler):
    def startElement(self, name, attrs):
        global functions
        
        if (name == 'profile'):
            functions = {}
        if (name == 'function'):
            hexaddr = attrs.get('addr')
            addr = int(hexaddr, 16)
            func = Function(addr, hexaddr, attrs)
            functions[addr] = func

def parse_asm(fl):
    global sourcemap
    sourcemap = {}
    
    lasttup = None
    while True:
        ln = fl.readline()
        if (not ln):
            break
        ln = ln.strip()
        ls = ln.split()
        if (lasttup and not ls):
            (linenum, funcname, addr) = lasttup
            sourcemap[addr] = (linenum, funcname)
        lasttup = None
        try:
            if (len(ls) >= 4 and ls[2] == '[' and ls[1].startswith('+')):
                linenum = int(ls[0])
                funcname = ls[3]
                addr = int(ls[1][1:], 16)
                lasttup = (linenum, funcname, addr)
        except ValueError:
            pass

class InformFunc:
    def __init__(self, funcnum):
        self.funcnum = funcnum
        self.name = '<???>'
        self.addr = 0
        self.linenum = None
        self.endaddr = None
        self.endlinenum = None
        self.locals = None
        self.seqpts = None
    def __repr__(self):
        return '<InformFunc $' + hex(self.addr)[2:] + ' ' + repr(self.name) + '>'
            
class DebugFile:
    def __init__(self, fl):
        self.files = {}
        self.functions = {}
        self.function_names = {}
        self.classes = []
        self.objects = {}
        self.arrays = {}
        self.globals = {}
        self.properties = {}
        self.attributes = {}
        self.actions = {}
        self.fake_actions = {}
        self.map = {}
        self.header = None
        
        dat = fl.read(2)
        val = unpack('>H', dat)[0]
        if (val != 0xDEBF):
            raise ValueError('not an Inform debug file')
            
        dat = fl.read(2)
        self.debugversion = unpack('>H', dat)[0]
        dat = fl.read(2)
        self.informversion = unpack('>H', dat)[0]

        rectable = {
            1:  self.read_file_rec,
            2:  self.read_class_rec,
            3:  self.read_object_rec,
            4:  self.read_global_rec,
            5:  self.read_attr_rec,
            6:  self.read_prop_rec,
            7:  self.read_fake_action_rec,
            8:  self.read_action_rec,
            9:  self.read_header_rec,
            10: self.read_lineref_rec,
            11: self.read_routine_rec,
            12: self.read_array_rec,
            13: self.read_map_rec,
            14: self.read_routine_end_rec,
        }

        while True:
            dat = fl.read(1)
            rectype = unpack('>B', dat)[0]
            if (rectype == 0):
                break
            recfunc = rectable.get(rectype)
            if (not recfunc):
                raise ValueError('unknown debug record type: %d' % (rectype,))
            recfunc(fl)

        for func in self.functions.values():
            self.function_names[func.name] = func

    def read_file_rec(self, fl):
        dat = fl.read(1)
        filenum = unpack('>B', dat)[0]
        includename = self.read_string(fl)
        realname = self.read_string(fl)
        self.files[filenum] = ( includename, realname )
        
    def read_class_rec(self, fl):
        name = self.read_string(fl)
        start = self.read_linenum(fl)
        end = self.read_linenum(fl)
        self.classes.append( (name, start, end) )
        
    def read_object_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        start = self.read_linenum(fl)
        end = self.read_linenum(fl)
        self.objects[num] = (name, start, end)
    
    def read_global_rec(self, fl):
        dat = fl.read(1)
        num = unpack('>B', dat)[0]
        name = self.read_string(fl)
        self.arrays[num] = name
    
    def read_array_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        self.arrays[num] = name
    
    def read_attr_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        self.attributes[num] = name
    
    def read_prop_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        self.properties[num] = name
    
    def read_action_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        self.actions[num] = name
    
    def read_fake_action_rec(self, fl):
        dat = fl.read(2)
        num = unpack('>H', dat)[0]
        name = self.read_string(fl)
        self.fake_actions[num] = name
    
    def read_routine_rec(self, fl):
        dat = fl.read(2)
        funcnum = unpack('>H', dat)[0]
        func = self.get_function(funcnum)
        
        func.linenum = self.read_linenum(fl)
        dat = fl.read(3)
        addr = unpack('>I', '\0'+dat)[0]
        func.addr = int(addr)
        func.name = self.read_string(fl)
        locals = []
        while True:
            val = self.read_string(fl)
            if (not val):
                break
            locals.append(val)
        func.locals = locals

    def read_lineref_rec(self, fl):
        dat = fl.read(2)
        funcnum = unpack('>H', dat)[0]
        func = self.get_function(funcnum)

        if (not func.seqpts):
            func.seqpts = []
        
        dat = fl.read(2)
        count = unpack('>H', dat)[0]
        for ix in range(count):
            linenum = self.read_linenum(fl)
            dat = fl.read(2)
            addr = unpack('>H', dat)[0]
            func.seqpts.append( (linenum, addr) )
        
    def read_routine_end_rec(self, fl):
        dat = fl.read(2)
        funcnum = unpack('>H', dat)[0]
        func = self.get_function(funcnum)

        func.endlinenum = self.read_linenum(fl)
        dat = fl.read(3)
        addr = unpack('>I', '\0'+dat)[0]
        func.endaddr = int(addr)

    def read_header_rec(self, fl):
        dat = fl.read(64)
        self.header = dat
    
    def read_map_rec(self, fl):
        while True:
            name = self.read_string(fl)
            if (not name):
                break
            dat = fl.read(3)
            addr = unpack('>I', '\0'+dat)[0]
            addr = int(addr)
            self.map[name] = addr
    
    def read_linenum(self, fl):
        dat = fl.read(4)
        (funcnum, linenum, charnum) = unpack('>BHB', dat)
        return (funcnum, linenum, charnum)

    def read_string(self, fl):
        val = ''
        while True:
            dat = fl.read(1)
            if (dat == '\0'):
                return val
            val += dat

    def get_function(self, funcnum):
        func = self.functions.get(funcnum)
        if (not func):
            func = InformFunc(funcnum)
            self.functions[funcnum] = func
        return func
                        
# Begin the work
            
xml.sax.parse(profile_raw, ProfileRawHandler())

source_start = min([ func.addr for func in functions.values()
    if not func.special ])
print 'Code segment begins at', hex(source_start)

print len(functions), 'called functions found in', profile_raw

if (game_asm):
    fl = open(game_asm, 'rb')
    val = fl.read(2)
    fl.close()
    if (val == '\xde\xbf'):
        fl = open(game_asm, 'rb')
        debugfile = DebugFile(fl)
        fl.close()
        sourcemap = {}
        for func in debugfile.functions.values():
            sourcemap[func.addr] = ( func.linenum[1], func.name)
    else:
        fl = open(game_asm, 'rU')
        parse_asm(fl)
        fl.close()

if (sourcemap):
    badls = []

    for (addr, func) in functions.items():
        if (func.special):
            continue
        tup = sourcemap.get(addr-source_start)
        if (not tup):
            badls.append(addr)
            continue
        (linenum, funcname) = tup
        func.name = funcname
        func.linenum = linenum
    
    if (badls):
        print len(badls), 'functions from', profile_raw, 'did not appear in asm (veneer functions)'
    
    function_names = {}
    for func in functions.values():
        function_names[func.name] = func

if (sourcemap):
    uncalled_funcs = [ funcname for (addr, (linenum, funcname)) in sourcemap.items() if (addr+source_start) not in functions ]
    print len(uncalled_funcs), 'functions found in', game_asm, 'were never called'

print 'Functions that consumed the most time (excluding children):'
ls = functions.values()
ls.sort(lambda x1, x2: cmp(x2.self_time, x1.self_time))
for func in ls[:10]:
    func.dump()

